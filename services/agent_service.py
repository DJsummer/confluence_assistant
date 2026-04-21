"""
LangChain Tool Calling Agent 服务（兼容 langchain>=1.x）
工具列表：
  - search_confluence : 向量检索 Confluence 知识库
  - get_jira_issue    : 查询 Jira issue 详情
  - get_pronto_pr     : 获取 Pronto PR 信息与链接
"""

import logging
from typing import Generator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from config.settings import settings
from services.jira_service import get_jira_issue
from services.pronto_service import get_pronto_pr
from services.retriever import retrieve

log = logging.getLogger(__name__)

_SYSTEM = SystemMessage(content="""\
你是公司内部 AI 助手，可以查阅 Confluence 知识库、Jira 任务系统和 Pronto 问题报告系统。

必须遵守的规则：
1. 所有回答必须基于工具返回的真实内容，严禁凭空编造，严禁使用工具以外的任何预训练知识。
2. 如果工具返回内容不足以回答，直接回复「我在知识库中未找到相关信息，建议查阅相关文档」，不要猜测或补充。
3. 如果问题包含 Jira key（如 FPB-123）或 Pronto ID（如 PR700839、755857 等），先用对应工具查询。
4. 其他技术问题必须先调用 search_confluence，根据工具返回内容作答。
5. 回答末尾仅附上工具返回的真实来源链接，禁止拼凑或推测 URL。
6. 使用中文，语言简洁专业，不超过 600 字。
""")

_MAX_ITER = 6


# ── Tool 定义 ─────────────────────────────────────────────────────────────────

@tool
def search_confluence(query: str) -> str:
    """搜索 Confluence 知识库，输入自然语言查询，返回相关文档片段。适合回答技术问题、流程说明、设计文档等。"""
    docs = retrieve(query, top_k=4)
    if not docs:
        return "未找到相关文档。"
    parts = [f"【{d['title']}】({d['url']})\n{d['text'][:800]}" for d in docs]
    return "\n\n---\n\n".join(parts)


@tool
def get_jira(key: str) -> str:
    """查询单个 Jira issue 的详情。输入 Jira key，如 FPB-1495109 或 FCA_OAMEFS-67106。返回标题、状态、优先级、描述和链接。"""
    result = get_jira_issue(key.strip())
    if result.get("forbidden"):
        return (
            f"无权限访问 {result['key']}（403 Forbidden），可能属于受限项目。"
            f"链接：{result['url']} "
            f"建议：请用 search_confluence 搜索相关关键词获取更多信息。"
        )
    if "error" in result:
        return f"查询失败：{result['error']}"
    return (
        f"**{result['key']}** - {result['summary']}\n"
        f"状态：{result['status']}  优先级：{result['priority']}  经办人：{result['assignee']}\n"
        f"链接：{result['url']}\n"
        f"描述：{result['description']}"
    )


@tool
def get_pronto(pr_id: str) -> str:
    """获取 Pronto 问题报告的详情。输入 PR ID，支持格式：PR755857、755857、02052295（带或不带 PR 前缀均可）。返回标题、状态、严重级别、经办人和链接。"""
    result = get_pronto_pr(pr_id.strip())
    if result.get("error") and result["title"] == result["pr_id"]:
        return f"查询失败：{result['error']}\n链接：{result['url']}"
    return (
        f"**{result['pr_id']}** - {result['title']}\n"
        f"状态：{result.get('status', '')}  严重级别：{result.get('severity', '')}  经办人：{result.get('assignee', '')}\n"
        f"链接：{result['url']}\n"
        f"R&D Info：{result.get('description', '')}"
    )


_TOOLS = [search_confluence, get_jira, get_pronto]
_TOOL_MAP = {t.name: t for t in _TOOLS}


# ── LLM 构建 ─────────────────────────────────────────────────────────────────

def _build_llm():
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.vllm_api_key,
        temperature=0.1,
        max_tokens=600,
    ).bind_tools(_TOOLS)


# ── Agent 执行循环 ────────────────────────────────────────────────────────────

def _build_messages(question: str, history: list[dict] | None) -> list:
    msgs = [_SYSTEM]
    for h in (history or [])[-6:]:
        if h["role"] == "user":
            msgs.append(HumanMessage(content=h["content"]))
        else:
            msgs.append(AIMessage(content=h["content"]))
    msgs.append(HumanMessage(content=question))
    return msgs


def run_agent(question: str, history: list[dict] | None = None) -> dict:
    """
    同步运行 Agent，返回 {"answer": str, "steps": [...]}
    """
    llm = _build_llm()
    messages = _build_messages(question, history)
    steps = []

    for _ in range(_MAX_ITER):
        response: AIMessage = llm.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            # 没有工具调用 → 最终回答
            return {"answer": response.content or "", "steps": steps}

        # 执行工具
        for tc in response.tool_calls:
            tool_fn = _TOOL_MAP.get(tc["name"])
            if tool_fn is None:
                output = f"未知工具：{tc['name']}"
            else:
                try:
                    output = tool_fn.invoke(tc["args"])
                except Exception as e:
                    output = f"工具调用出错：{e}"

            steps.append({
                "tool": tc["name"],
                "input": str(tc["args"]),
                "output": str(output)[:300],
            })
            messages.append(ToolMessage(content=str(output), tool_call_id=tc["id"]))

    return {"answer": "已达最大迭代次数，无法完成回答。", "steps": steps}


def run_agent_stream(question: str, history: list[dict] | None = None) -> Generator[dict, None, None]:
    """
    流式运行 Agent，yield 事件 dict：
      {"type": "step",   "tool": str, "input": str}
      {"type": "result", "tool": str, "output": str}
      {"type": "answer", "text": str}
      {"type": "done"}
    """
    llm = _build_llm()
    messages = _build_messages(question, history)

    for _ in range(_MAX_ITER):
        response: AIMessage = llm.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            yield {"type": "answer", "text": response.content or ""}
            break

        for tc in response.tool_calls:
            yield {"type": "step", "tool": tc["name"], "input": str(tc["args"])}

            tool_fn = _TOOL_MAP.get(tc["name"])
            if tool_fn is None:
                output = f"未知工具：{tc['name']}"
            else:
                try:
                    output = tool_fn.invoke(tc["args"])
                except Exception as e:
                    output = f"工具调用出错：{e}"

            yield {"type": "result", "tool": tc["name"], "output": str(output)[:300]}
            messages.append(ToolMessage(content=str(output), tool_call_id=tc["id"]))
    else:
        yield {"type": "answer", "text": "已达最大迭代次数，无法完成回答。"}

    yield {"type": "done"}

