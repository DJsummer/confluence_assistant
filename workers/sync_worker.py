"""
Celery 异步同步 Worker
任务：
    sync_confluence  - 抓取 Confluence → 切分 → embed → 写入向量库 + 元数据库
定时：每 6 小时自动增量同步（可在 .env 中调整）
"""

import logging

from celery import Celery
from celery.schedules import crontab

from config.settings import settings
from db.metadata_db import get_last_sync, upsert_doc_meta
from db.vector_store import delete_by_doc_id, upsert_chunks
from services.chunker import chunk_document
from services.confluence_loader import fetch_all_pages
from services.embedding_service import embed_batch

log = logging.getLogger(__name__)

app = Celery(
    "rag_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)


# ─────────────────────── 定时任务 ────────────────────────────────────────────

@app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    # 每 6 小时增量同步一次
    sender.add_periodic_task(
        6 * 3600,
        sync_confluence.s(settings.root_title, settings.space_key, False),
        name="confluence-incremental-sync",
    )


# ─────────────────────── 核心任务 ────────────────────────────────────────────

@app.task(bind=True, max_retries=3, default_retry_delay=60)
def sync_confluence(self, root_title: str, space: str, full_sync: bool = False):
    """
    root_title: Confluence 根页面标题
    space:      Space Key
    full_sync:  True = 忽略 last_sync，全量同步
    """
    try:
        last_sync = None if full_sync else get_last_sync()
        log.info(
            "sync_confluence start: root=%s space=%s full=%s last_sync=%s",
            root_title, space, full_sync, last_sync,
        )

        pages = fetch_all_pages(root_title, space, last_sync=last_sync)
        log.info("Fetched %d pages to index", len(pages))

        success, failed = 0, 0
        for doc in pages:
            try:
                _index_document(doc)
                success += 1
            except Exception as exc:
                log.error("Failed to index doc %s (%s): %s", doc["id"], doc.get("title"), exc)
                failed += 1

        log.info("Sync done: success=%d failed=%d", success, failed)
        return {"success": success, "failed": failed}

    except Exception as exc:
        log.error("sync_confluence error: %s", exc)
        raise self.retry(exc=exc)


def _index_document(doc: dict) -> None:
    """单文档：存元数据 → 删旧向量 → 切分 → embed → 写向量库。"""
    # 1. 元数据
    upsert_doc_meta(doc)

    # 2. 删除旧向量（避免重复）
    delete_by_doc_id(doc["id"])

    # 3. 切分
    chunks = chunk_document(doc, settings.chunk_size, settings.chunk_overlap)
    if not chunks:
        log.debug("No chunks for doc %s, skipped", doc["id"])
        return

    # 4. 批量 embed
    texts = [c.text for c in chunks]
    vectors = embed_batch(texts)

    # 5. 写入向量库
    records = [
        {
            "chunk_id":    f"{c.doc_id}_{c.chunk_index}",
            "doc_id":      c.doc_id,
            "title":       c.title,
            "path":        c.path,
            "url":         c.url,
            "text":        c.text,
            "permissions": doc.get("permissions", []),
            "vector":      vec,
        }
        for c, vec in zip(chunks, vectors)
    ]
    upsert_chunks(records)
    log.info("Indexed %d chunks for: %s", len(records), doc["title"])
