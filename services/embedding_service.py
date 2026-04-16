"""
Embedding 服务（本地推理 bge-m3）
- 使用 sentence-transformers 本地加载 BAAI/bge-m3
- Redis 缓存（7 天），避免重复推理开销
- 支持单条 / 批量接口
- 无网络依赖，不需要任何 API Key
"""

import hashlib
import json
import logging
from typing import Optional

import redis

from config.settings import settings

log = logging.getLogger(__name__)

_redis_client = redis.from_url(settings.redis_url, decode_responses=True)
_CACHE_TTL = 86400 * 7  # 7 天

# 延迟加载模型（首次调用时初始化，避免 import 阶段占用显存）
_model: Optional[object] = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        log.info(
            "Loading embedding model: %s  device=%s",
            settings.embedding_model,
            settings.embedding_device,
        )
        _model = SentenceTransformer(
            settings.embedding_model,
            device=settings.embedding_device,
            cache_folder=settings.hf_cache_dir,
        )
        log.info("Embedding model loaded, dim=%d", _model.get_sentence_embedding_dimension())
    return _model


def _cache_key(text: str) -> str:
    # model 名称做命名空间隔离，模型换了自动失效
    model_tag = settings.embedding_model.replace("/", "_")
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()
    return f"embed:{model_tag}:{digest}"


# ─────────────────────── 单条 ────────────────────────────────────────────────

def embed(text: str) -> list[float]:
    key = _cache_key(text)
    cached = _redis_client.get(key)
    if cached:
        return json.loads(cached)

    vec: list[float] = _get_model().encode(
        text,
        normalize_embeddings=True,
        show_progress_bar=False,
    ).tolist()
    _redis_client.setex(key, _CACHE_TTL, json.dumps(vec))
    return vec


# ─────────────────────── 批量 ──────────────────────────────────────────────

def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    批量 embed：命中缓存的直接返回，未命中的合并推理（减少 GPU 调度开销）。
    """
    results: list[list[float] | None] = [None] * len(texts)
    uncached_indices: list[int] = []
    uncached_texts: list[str] = []

    for i, text in enumerate(texts):
        key = _cache_key(text)
        cached = _redis_client.get(key)
        if cached:
            results[i] = json.loads(cached)
        else:
            uncached_indices.append(i)
            uncached_texts.append(text)

    if uncached_texts:
        log.debug("Embedding %d uncached texts (local inference)", len(uncached_texts))
        model = _get_model()
        batch_size = settings.embedding_batch_size
        all_vecs: list[list[float]] = []
        for start in range(0, len(uncached_texts), batch_size):
            batch = uncached_texts[start : start + batch_size]
            vecs = model.encode(
                batch,
                batch_size=batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            ).tolist()
            all_vecs.extend(vecs)

        for i, (idx, vec) in enumerate(zip(uncached_indices, all_vecs)):
            key = _cache_key(uncached_texts[i])
            _redis_client.setex(key, _CACHE_TTL, json.dumps(vec))
            results[idx] = vec

    return results  # type: ignore[return-value]
