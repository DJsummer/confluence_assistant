"""
Pronto 服务（使用 REST API）
- get_pronto_pr(pr_id)  → 通过 Pronto REST API 获取 PR 详情
- extract_pronto_ids()  → 从文本中提取 Pronto PR ID

REST API: https://pronto.int.net.nokia.com/prontoapi/rest/api/1/problemReport/{id}
认证方式: HTTPBasicAuth (AD 账号 + 密码)
"""

import logging
import re

import requests
from requests.auth import HTTPBasicAuth

from config.settings import settings

log = logging.getLogger(__name__)

def _api_base() -> str:
    return f"{settings.pronto_base}/prontoapi/rest/api/1/problemReport"


def _web_base() -> str:
    return f"{settings.pronto_base}/pronto/problemReport.html"


def _pronto_auth() -> HTTPBasicAuth:
    return HTTPBasicAuth(settings.pronto_user, settings.pronto_token)


def get_pronto_pr(pr_id: str) -> dict:
    """
    通过 Pronto REST API 查询 PR 详情。
    返回 {"pr_id", "title", "status", "severity", "assignee", "description", "url", "error"(可选)}
    """
    pr_id = pr_id.strip().upper()
    if not pr_id.startswith("PR"):
        pr_id = "PR" + pr_id

    api_url = f"{_api_base()}/{pr_id}"
    web_url = f"{_web_base()}?prid={pr_id}&showGF="

    try:
        resp = requests.get(
            api_url,
            auth=_pronto_auth(),
            timeout=10,
            verify=False,  # Nokia 内网证书
        )
        resp.raise_for_status()
        data = resp.json()

        return {
            "pr_id": pr_id,
            "title": data.get("title") or data.get("synopsis") or pr_id,
            "status": data.get("status", ""),
            "severity": data.get("severity", ""),
            "assignee": data.get("assignee") or data.get("responsible", ""),
            "description": (data.get("rdInfo") or data.get("description") or "")[:500],
            "raw": data,
            "url": web_url,
        }
    except requests.HTTPError as e:
        log.warning("Pronto API %s: %s", pr_id, e)
        return {"pr_id": pr_id, "title": pr_id, "url": web_url, "error": str(e)}
    except Exception as e:
        log.warning("Pronto API %s: %s", pr_id, e)
        return {"pr_id": pr_id, "title": pr_id, "url": web_url, "error": str(e)}


# ── 文本中提取 Pronto ID ──────────────────────────────────────────────────────

# PR700839 / pr700839（PR前缀）或 02052295（纯数字，7位以上）
_PRONTO_PR_PREFIX = re.compile(r"\b(PR\d{4,})\b", re.I)
_PRONTO_NUMERIC   = re.compile(r"(?<![\w/-])(\d{7,9})(?![\w/-])")


def extract_pronto_ids(text: str) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for m in _PRONTO_PR_PREFIX.findall(text):
        key = m.upper()
        if key not in seen:
            seen.add(key)
            ids.append(key)
    for m in _PRONTO_NUMERIC.findall(text):
        key = "PR" + m
        if key not in seen:
            seen.add(key)
            ids.append(key)
    return ids
