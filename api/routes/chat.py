"""
Chat 路由
POST /chat      - 问答（含多轮历史）
POST /chat/stream - 流式问答（SSE）
"""

import asyncio
import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services.llm_service import generate, generate_stream
from services.retriever import retrieve

log = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    question:    str
    user_groups: list[str] = Field(default_factory=list, description="用户所属权限组")
    history:     list[dict] = Field(default_factory=list, description="多轮对话历史")
    top_k:       int = Field(default=5, ge=1, le=20)


class Source(BaseModel):
    title: str
    path:  str
    url:   str
    score: float = 0.0


class ChatResponse(BaseModel):
    answer:  str
    sources: list[Source]


@router.post("/", response_model=ChatResponse)
async def chat(req: ChatRequest):
    log.info("chat: question=%r groups=%s", req.question[:80], req.user_groups)

    loop = asyncio.get_event_loop()
    docs = await loop.run_in_executor(
        None, retrieve, req.question, req.user_groups, req.top_k
    )
    if not docs:
        return ChatResponse(
            answer="未找到相关文档，无法回答该问题。",
            sources=[],
        )

    answer = generate(docs, req.question, history=req.history)
    sources = [
        Source(
            title=d["title"],
            path=d["path"],
            url=d["url"],
            score=d.get("score", 0.0),
        )
        for d in docs
    ]
    return ChatResponse(answer=answer, sources=sources)


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    """流式问答，返回 SSE（Server-Sent Events）"""
    log.info("chat_stream: question=%r", req.question[:80])

    loop = asyncio.get_event_loop()
    docs = await loop.run_in_executor(
        None, retrieve, req.question, req.user_groups, req.top_k
    )

    sources = [
        {"title": d["title"], "path": d["path"], "url": d["url"], "score": d.get("score", 0.0)}
        for d in docs
    ]

    async def event_gen():
        if not docs:
            yield f"data: {json.dumps({'delta': '未找到相关文档，无法回答该问题。'})}\n\n"
            yield f"data: {json.dumps({'done': True, 'sources': []})}\n\n"
            return

        try:
            for chunk in generate_stream(docs, req.question, history=req.history):
                yield f"data: {json.dumps({'delta': chunk})}\n\n"
                await asyncio.sleep(0)  # 让出事件循环，保持连接活跃
        except Exception as e:
            log.error("stream error: %s", e)
            yield f"data: {json.dumps({'delta': f'[错误: {e}]'})}\n\n"
        finally:
            yield f"data: {json.dumps({'done': True, 'sources': sources})}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
