"""
Milvus 向量数据库操作封装
Collection Schema:
    chunk_id    VARCHAR  PK
    doc_id      VARCHAR
    title       VARCHAR
    path        VARCHAR
    url         VARCHAR
    text        VARCHAR  (最多 8192 字符)
    permissions ARRAY<VARCHAR>
    vector      FLOAT_VECTOR(dim)

索引：HNSW COSINE
"""

import logging
from typing import Optional

from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    connections,
    utility,
)

from config.settings import settings

log = logging.getLogger(__name__)

_COLLECTION = settings.milvus_collection
_DIM = settings.vector_dim


def _connect():
    connections.connect(
        alias="default",
        host=settings.milvus_host,
        port=settings.milvus_port,
    )


def _create_collection() -> Collection:
    fields = [
        FieldSchema("chunk_id",    DataType.VARCHAR,      max_length=256,   is_primary=True, auto_id=False),
        FieldSchema("doc_id",      DataType.VARCHAR,      max_length=128),
        FieldSchema("title",       DataType.VARCHAR,      max_length=512),
        FieldSchema("path",        DataType.VARCHAR,      max_length=4096),
        FieldSchema("url",         DataType.VARCHAR,      max_length=2048),
        FieldSchema("text",        DataType.VARCHAR,      max_length=65535),
        FieldSchema("permissions", DataType.ARRAY,
                    element_type=DataType.VARCHAR,
                    max_capacity=64,
                    max_length=128),
        FieldSchema("vector",      DataType.FLOAT_VECTOR, dim=_DIM),
    ]
    schema = CollectionSchema(fields, description="Confluence RAG chunks")
    col = Collection(_COLLECTION, schema)
    col.create_index(
        "vector",
        {
            "metric_type": "COSINE",
            "index_type": "HNSW",
            "params": {"M": 16, "efConstruction": 256},
        },
    )
    log.info("Collection %s created with HNSW index", _COLLECTION)
    return col


def get_collection() -> Collection:
    _connect()
    if not utility.has_collection(_COLLECTION):
        _create_collection()
    col = Collection(_COLLECTION)
    col.load()
    return col


def drop_and_recreate() -> None:
    """删除旧 collection 并重建（schema 变更时使用）。"""
    _connect()
    if utility.has_collection(_COLLECTION):
        utility.drop_collection(_COLLECTION)
        log.warning("Dropped collection: %s", _COLLECTION)
    _create_collection()
    log.info("Recreated collection: %s", _COLLECTION)


# ─────────────────────── 字节安全截断 ─────────────────────────────────────────

def _safe_truncate(s: str, max_bytes: int) -> str:
    """截断字符串使其 UTF-8 编码不超过 max_bytes 字节（避免截断多字节字符乱码）。"""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


# ─────────────────────── 写入 ────────────────────────────────────────────────

def upsert_chunks(chunks: list[dict]) -> None:
    """
    chunks: list of dicts，每条含
        chunk_id / doc_id / title / path / url / text / permissions / vector
    """
    if not chunks:
        return
    col = get_collection()

    # 转列式存储（所有 VARCHAR 做字节安全截断，中文 3 字节/字符）
    data = {
        "chunk_id":    [_safe_truncate(c["chunk_id"], 250)    for c in chunks],
        "doc_id":      [_safe_truncate(c["doc_id"],   120)    for c in chunks],
        "title":       [_safe_truncate(c["title"],    500)    for c in chunks],
        "path":        [_safe_truncate(c["path"],    4000)    for c in chunks],
        "url":         [_safe_truncate(c["url"],     2000)    for c in chunks],
        "text":        [_safe_truncate(c["text"],   65000)   for c in chunks],
        "permissions": [c.get("permissions", [])               for c in chunks],
        "vector":      [c["vector"]                           for c in chunks],
    }
    col.upsert(list(data.values()))
    log.info("Upserted %d chunks to Milvus", len(chunks))


def delete_by_doc_id(doc_id: str) -> None:
    col = get_collection()
    col.delete(expr=f'doc_id == "{doc_id}"')
    log.debug("Deleted chunks for doc_id=%s", doc_id)


# ─────────────────────── 查询 ────────────────────────────────────────────────

def search(
    vector: list[float],
    top_k: int = 10,
    filter_expr: str = "",
) -> list[dict]:
    col = get_collection()
    results = col.search(
        data=[vector],
        anns_field="vector",
        param={"metric_type": "COSINE", "params": {"ef": 256}},
        limit=top_k,
        expr=filter_expr or None,
        output_fields=["chunk_id", "doc_id", "title", "path", "url", "text"],
    )
    hits = []
    for hit in results[0]:
        hits.append({
            "chunk_id": hit.entity.get("chunk_id"),
            "doc_id":   hit.entity.get("doc_id"),
            "title":    hit.entity.get("title"),
            "path":     hit.entity.get("path"),
            "url":      hit.entity.get("url"),
            "text":     hit.entity.get("text"),
            "score":    float(hit.score),
        })
    return hits
