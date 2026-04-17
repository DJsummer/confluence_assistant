"""
Jira 服务
- get_jira_issue(key)  → 查询 Jira ticket 详情
- extract_jira_keys()  → 从文本中提取 Jira ID
"""

import logging
import re

import requests
from requests.auth import HTTPBasicAuth

from config.settings import settings

log = logging.getLogger(__name__)


def _jira_auth() -> HTTPBasicAuth:
    return HTTPBasicAuth(settings.jira_user, settings.jira_token)


def get_jira_issue(key: str) -> dict:
    """
    查询 Jira issue。
    返回 {"key", "summary", "status", "assignee", "priority", "description", "url"}
    出错时返回 {"error": "..."}
    """
    key = key.strip().upper()
    url = f"{settings.jira_base}/rest/api/2/issue/{key}"
    try:
        resp = requests.get(
            url,
            auth=_jira_auth(),
            timeout=10,
            verify=False,   # Nokia 内网证书
        )
        if resp.status_code == 404:
            return {"error": f"Jira issue {key} 不存在"}
        resp.raise_for_status()
        data = resp.json()

        fields = data.get("fields", {})
        description = fields.get("description") or ""
        if len(description) > 800:
            description = description[:800] + "…"

        return {
            "key": key,
            "summary": fields.get("summary", ""),
            "status": (fields.get("status") or {}).get("name", ""),
            "assignee": ((fields.get("assignee") or {}).get("displayName", "未分配")),
            "priority": ((fields.get("priority") or {}).get("name", "")),
            "description": description,
            "url": f"{settings.jira_base}/browse/{key}",
        }
    except requests.RequestException as e:
        log.warning("Jira API error for %s: %s", key, e)
        return {"error": str(e)}


# ── 文本中提取 Jira ID ────────────────────────────────────────────────────────

# FPB-12345 / FCA_OAMEFS-67106 / UICA-123
_JIRA_PATTERN = re.compile(r"\b([A-Z][A-Z0-9_]+-\d+)\b")


def extract_jira_keys(text: str) -> list[str]:
    return list(dict.fromkeys(_JIRA_PATTERN.findall(text)))
