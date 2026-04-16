"""
混合检索服务（向量检索 + Reranker）
流程：
    1. embed(query) → Milvus 向量搜索 top_k*2（含权限过滤）
    2. CrossEncoder reranker 重排
    3. 返回 top_k 结果
    4. 结果缓存 5 分钟（Redis）
"""

import hashlib
import json
import logging
from typing import Optional

import redis

from config.settings import settings
from db.vector_store import search as vector_search
from services.embedding_service import embed

log = logging.getLogger(__name__)

_redis_client = redis.from_url(settings.redis_url, decode_responses=True)
_QUERY_CACHE_TTL = 300  # 5 分钟

# Reranker（延迟加载，避免启动时间过长）
_reranker = None


def _get_reranker():
    global _reranker
    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder
            _reranker = CrossEncoder(
                "cross-encoder/ms-marco-MiniLM-L-6-v2",
                cache_folder=settings.hf_cache_dir,
            )
            log.info("Reranker loaded")
        except Exception as e:
            log.warning("Reranker unavailable, skip rerank: %s", e)
    return _reranker


def _query_cache_key(query: str, user_groups: list[str]) -> str:
    raw = query + "|" + ",".join(sorted(user_groups))
    return "qcache:" + hashlib.md5(raw.encode()).hexdigest()


def retrieve(
    query: str,
    user_groups: Optional[list[str]] = None,
    top_k: int = 5,
) -> list[dict]:
    """
    query:       用户问题
    user_groups: 当前用户所属权限组（用于 metadata 过滤），None 表示不过滤
    top_k:       最终返回条数
    """
    groups = user_groups or []

    # ─ 查询缓存 ──────────────────────────────────────────────
    cache_key = _query_cache_key(query, groups)
    cached = _redis_client.get(cache_key)
    if cached:
        log.debug("Query cache hit")
        return json.loads(cached)

    # ─ 权限过滤表达式（Milvus expr 语法）───────────────────
    filter_expr = ""
    if groups:
        filter_expr = " or ".join(
            [f'array_contains(permissions, "{g}")' for g in groups]
        )

    # ─ 向量检索 ──────────────────────────────────────────────
    q_vec = embed(query)
    hits = vector_search(q_vec, top_k=top_k * 3, filter_expr=filter_expr)

    if not hits:
        return []

    # ─ Rerank ────────────────────────────────────────────────
    reranker = _get_reranker()
    if reranker:
        pairs = [(query, h["text"]) for h in hits]
        scores = reranker.predict(pairs)
        ranked = sorted(zip(scores, hits), key=lambda x: x[0], reverse=True)
        hits = [h for _, h in ranked]

    results = hits[:top_k]

    # ─ 写入缓存 ──────────────────────────────────────────────
    _redis_client.setex(cache_key, _QUERY_CACHE_TTL, json.dumps(results))
    return results
