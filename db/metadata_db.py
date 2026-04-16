"""
PostgreSQL 元数据库
存储页面级元数据（不含向量），用于增量同步判断、权限管理、来源展示。
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Column, DateTime, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.pool import NullPool

from config.settings import settings

log = logging.getLogger(__name__)


def _make_engine():
    """每次调用返回新 engine，NullPool 避免 fork 子进程复用连接。"""
    return create_engine(
        settings.postgres_dsn,
        poolclass=NullPool,   # Celery fork 安全：每次操作独立连接
        pool_pre_ping=True,
    )


engine = _make_engine()


class Base(DeclarativeBase):
    pass


class DocMeta(Base):
    __tablename__ = "doc_meta"

    doc_id      = Column(String(128),  primary_key=True)
    title       = Column(String(512),  nullable=False)
    path        = Column(String(1024), nullable=True)
    url         = Column(String(1024), nullable=True)
    space       = Column(String(64),   nullable=True)
    permissions = Column(JSON,         nullable=True, default=list)
    updated_at  = Column(DateTime(timezone=True), nullable=True)
    synced_at   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# 建表（幂等，延迟到首次使用时执行）
_tables_created = False


def _ensure_tables() -> None:
    global _tables_created
    if not _tables_created:
        Base.metadata.create_all(engine)
        _tables_created = True


# ─────────────────────── CRUD ────────────────────────────────────────────────

def upsert_doc_meta(doc: dict) -> None:
    _ensure_tables()
    updated_at: Optional[datetime] = None
    if doc.get("updated_at"):
        try:
            updated_at = datetime.fromisoformat(doc["updated_at"].replace("Z", "+00:00"))
        except ValueError:
            pass

    with Session(engine) as sess:
        meta = DocMeta(
            doc_id      = doc["id"],
            title       = doc.get("title", ""),
            path        = doc.get("path", ""),
            url         = doc.get("url", ""),
            space       = doc.get("space", ""),
            permissions = doc.get("permissions", []),
            updated_at  = updated_at,
            synced_at   = datetime.now(timezone.utc),
        )
        sess.merge(meta)
        sess.commit()


def get_last_sync() -> Optional[datetime]:
    """返回最近一次同步时间（用于增量同步判断）。"""
    _ensure_tables()
    with Session(engine) as sess:
        row = (
            sess.query(DocMeta.synced_at)
            .order_by(DocMeta.synced_at.desc())
            .first()
        )
        return row[0] if row else None


def soft_delete(doc_id: str) -> None:
    """标记删除（保留记录，只清 url，向量库另行 delete）。"""
    _ensure_tables()
    with Session(engine) as sess:
        meta = sess.get(DocMeta, doc_id)
        if meta:
            meta.url = ""
            sess.commit()
