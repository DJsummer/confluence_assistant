"""
LLM 服务
- 调用 OpenAI-compatible API（可换本地部署模型）
- 固定系统提示：引用限制 + 不确定时拒绝 + 附加出处
- 支持多轮对话 history（保留最近 N 轮）
"""

import logging

from openai import OpenAI

from config.settings import settings

log = logging.getLogger(__name__)

# vLLM 提供 OpenAI-compatible API，api_key 填任意非空字符串
_client = OpenAI(
    api_key=settings.vllm_api_key,
    base_url=settings.llm_base_url,
)

_SYSTEM_PROMPT = """\
你是UICA AI 助手，专门解答基于 Confluence 知识库的问题。

必须遵守的规则：
1. 只能根据下方「参考资料」作答，不得凭空编造内容。
2. 如果资料不足以回答，直接回复「我不知道」，不要猜测。
3. 回答末尾必须附上引用来源（格式：[标题](URL)）。
4. 使用中文回答，语言简洁专业。
5. 回答总字数不超过 500 字，禁止重复同一段落。
"""

_MAX_HISTORY_TURNS = 6  # 保留最近 3 轮对话（user + assistant 各算 1 条）


def generate(
    context_docs: list[dict],
    question: str,
    history: list[dict] | None = None,
) -> str:
    """
    context_docs: 检索结果列表，每条含 title / path / url / text
    question:     当前用户问题
    history:      多轮对话历史（[{"role": "user/assistant", "content": "..."}]）
    """
    # 拼接参考资料
    context_parts = []
    for i, doc in enumerate(context_docs, 1):
        context_parts.append(
            f"[{i}] 《{doc['title']}》路径：{doc['path']}\n{doc['text']}"
        )
    context = "\n\n---\n\n".join(context_parts)

    messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]

    # 多轮历史（截取最近 N 条）
    if history:
        messages.extend(history[-_MAX_HISTORY_TURNS:])

    messages.append({
        "role": "user",
        "content": f"参考资料：\n{context}\n\n问题：{question}",
    })

    resp = _client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=0.1,
        max_tokens=512,
        stop=["\n\n\n"],
    )
    answer: str = resp.choices[0].message.content or ""
    log.debug("LLM generated %d chars", len(answer))
    return answer


def generate_stream(
    context_docs: list[dict],
    question: str,
    history: list[dict] | None = None,
):
    """流式生成，yield 文本片段（供 SSE 流式传输）"""
    context_parts = []
    for i, doc in enumerate(context_docs, 1):
        context_parts.append(
            f"[{i}] 《{doc['title']}》路径：{doc['path']}\n{doc['text']}"
        )
    context = "\n\n---\n\n".join(context_parts)

    messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if history:
        messages.extend(history[-_MAX_HISTORY_TURNS:])
    messages.append({
        "role": "user",
        "content": f"参考资料：\n{context}\n\n问题：{question}",
    })

    stream = _client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=0.1,
        max_tokens=512,
        stop=["\n\n\n"],
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
