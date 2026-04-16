"""
同步触发路由
POST /sync          - 手动触发同步任务
GET  /sync/{task_id} - 查询任务状态
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from workers.sync_worker import sync_confluence

log = logging.getLogger(__name__)
router = APIRouter(prefix="/sync", tags=["sync"])


class SyncRequest(BaseModel):
    root_title: str = "FA_EM_SERVICE"
    space:      str = "UICA"
    full_sync:  bool = False


class SyncResponse(BaseModel):
    task_id: str
    status:  str


@router.post("/", response_model=SyncResponse)
async def trigger_sync(req: SyncRequest):
    task = sync_confluence.delay(req.root_title, req.space, req.full_sync)
    log.info("Sync task queued: task_id=%s", task.id)
    return SyncResponse(task_id=task.id, status="queued")


@router.get("/{task_id}", response_model=SyncResponse)
async def sync_status(task_id: str):
    result = sync_confluence.AsyncResult(task_id)
    return SyncResponse(task_id=task_id, status=result.status)
