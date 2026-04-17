"""
Agent 路由
POST /agent/stream  - 流式 Agent 问答（SSE），逐步展示工具调用过程
POST /agent         - 同步 Agent 问答（返回完整结果）
"""

import asyncio
import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services.agent_service import run_agent, run_agent_stream

log = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])


class AgentRequest(BaseModel):
    question: str
    history:  list[dict] = Field(default_factory=list)
    top_k:    int = Field(default=5, ge=1, le=20)


class AgentStep(BaseModel):
    tool:   str
    input:  str
    output: str = ""


class AgentResponse(BaseModel):
    answer: str
    steps:  list[AgentStep]


@router.post("/", response_model=AgentResponse)
async def agent_chat(req: AgentRequest):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, run_agent, req.question, req.history
    )
    return AgentResponse(
        answer=result["answer"],
        steps=[AgentStep(**s) for s in result["steps"]],
    )


@router.post("/stream")
async def agent_stream(req: AgentRequest):
    """
    SSE 流，每次 yield 一个 JSON 事件：
      {"type": "step",   "tool": "...", "input": "..."}
      {"type": "result", "tool": "...", "output": "..."}
      {"type": "answer", "text": "..."}
      {"type": "done"}
    """
    async def event_gen():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _run():
            try:
                for event in run_agent_stream(req.question, req.history):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "text": str(e)})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

        asyncio.get_event_loop().run_in_executor(None, _run)

        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
