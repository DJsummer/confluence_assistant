"""
Chat 路由
POST /chat      - 问答（含多轮历史）
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services.llm_service import generate
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

    docs = retrieve(req.question, user_groups=req.user_groups, top_k=req.top_k)
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
