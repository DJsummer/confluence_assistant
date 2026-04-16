"""
文档切分模块
策略：按段落边界切分 + token 长度控制 + overlap + 保留标题路径 prefix
输出 Chunk 列表，每条记录含完整上下文，适合 embedding 和 RAG 检索。
"""

from dataclasses import dataclass


@dataclass
class Chunk:
    doc_id: str
    chunk_index: int
    title: str
    path: str
    url: str
    text: str          # 含 [path] prefix，直接用于 embedding
    token_count: int


# ─────────────────────── Token 计数（可选 tiktoken）────────────────────────

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")

    def _count_tokens(text: str) -> int:
        return len(_enc.encode(text))

except ImportError:
    # fallback：按字符数估算（中文约 1 char ≈ 1 token）
    def _count_tokens(text: str) -> int:  # type: ignore[misc]
        return len(text)


# ─────────────────────── 核心切分逻辑 ────────────────────────────────────────

def chunk_document(
    doc: dict,
    chunk_size: int = 512,
    overlap: int = 64,
) -> list[Chunk]:
    """
    将单个页面文档切分为 Chunk 列表。

    - doc: 含 id / title / path / url / content 的字典
    - chunk_size: 每块最大 token 数
    - overlap: 相邻块重叠 token 数（保留上文语境）
    """
    title = doc.get("title", "")
    path = doc.get("path", "")
    url = doc.get("url", "")
    doc_id = doc.get("id", "")
    raw_text = doc.get("content", "").strip()

    if not raw_text:
        return []

    header = f"[{path}]\n"
    header_tokens = _count_tokens(header)
    effective_size = chunk_size - header_tokens  # 留给正文的 token 预算

    # 按双换行切段落，再按单换行切行（表格行属于"行"而不是"段落"）
    paragraphs: list[str] = []
    for block in raw_text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        # 表格块（含 | 分隔符）整体保留，不再二次拆行
        if " | " in block:
            paragraphs.append(block)
        else:
            for line in block.split("\n"):
                line = line.strip()
                if line:
                    paragraphs.append(line)

    chunks: list[Chunk] = []
    buffer: list[str] = []
    buffer_tokens = 0
    chunk_idx = 0

    def _flush(buf: list[str], idx: int) -> Chunk:
        body = "\n".join(buf)
        full_text = header + body
        return Chunk(
            doc_id=doc_id,
            chunk_index=idx,
            title=title,
            path=path,
            url=url,
            text=full_text,
            token_count=_count_tokens(full_text),
        )

    for para in paragraphs:
        para_tokens = _count_tokens(para)

        # 单段落超过 chunk_size，强制单独成块
        if para_tokens >= effective_size:
            if buffer:
                chunks.append(_flush(buffer, chunk_idx))
                chunk_idx += 1
            chunks.append(
                Chunk(
                    doc_id=doc_id,
                    chunk_index=chunk_idx,
                    title=title,
                    path=path,
                    url=url,
                    text=header + para,
                    token_count=_count_tokens(header + para),
                )
            )
            chunk_idx += 1
            buffer, buffer_tokens = [], 0
            continue

        if buffer_tokens + para_tokens > effective_size and buffer:
            chunks.append(_flush(buffer, chunk_idx))
            chunk_idx += 1

            # overlap：保留 buffer 末尾若干 token 的段落
            overlap_buf: list[str] = []
            overlap_tokens = 0
            for item in reversed(buffer):
                t = _count_tokens(item)
                if overlap_tokens + t > overlap:
                    break
                overlap_buf.insert(0, item)
                overlap_tokens += t
            buffer, buffer_tokens = overlap_buf, overlap_tokens

        buffer.append(para)
        buffer_tokens += para_tokens

    if buffer:
        chunks.append(_flush(buffer, chunk_idx))

    return chunks
