"""
Pronto 服务
- get_pronto_pr(pr_id)  → 构造 Pronto PR 链接并尝试抓取标题
- extract_pronto_ids()  → 从文本中提取 Pronto PR ID
"""

import logging
import re

import requests
from requests.auth import HTTPBasicAuth

from config.settings import settings

log = logging.getLogger(__name__)


def _pronto_auth() -> HTTPBasicAuth:
    return HTTPBasicAuth(settings.pronto_user, settings.pronto_token)


def get_pronto_pr(pr_id: str) -> dict:
    """
    构造 Pronto PR 链接，并尝试抓取标题（无 API 时降级为只返回链接）。
    返回 {"pr_id", "title", "url"}
    """
    pr_id = pr_id.strip().upper()
    if not pr_id.startswith("PR"):
        pr_id = "PR" + pr_id

    url = (
        f"{settings.pronto_base}/pronto/problemReport.html"
        f"?prid={pr_id}&showGF="
    )

    title = pr_id  # 默认标题
    try:
        resp = requests.get(
            url,
            auth=_pronto_auth(),
            timeout=8,
            verify=False,   # Nokia 内网证书
        )
        if resp.ok:
            # 从 HTML <title> 提取标题
            m = re.search(r"<title[^>]*>([^<]+)</title>", resp.text, re.I)
            if m:
                raw = m.group(1).strip()
                # Nokia SSO 登录页关键词 → 说明认证失败，保持默认标题
                _LOGIN_KEYWORDS = ("sign in", "log in", "login", "sso", "authenticate")
                if any(kw in raw.lower() for kw in _LOGIN_KEYWORDS):
                    log.debug("Pronto %s: got login page, skip title extraction", pr_id)
                else:
                    # 去掉 "Pronto - " 前缀
                    title = re.sub(r"^Pronto\s*[-–]\s*", "", raw)
    except Exception as e:
        log.debug("Pronto fetch skipped for %s: %s", pr_id, e)

    return {"pr_id": pr_id, "title": title, "url": url}


# ── 文本中提取 Pronto ID ──────────────────────────────────────────────────────

# PR700839 / pr700839
_PRONTO_PATTERN = re.compile(r"\b(PR\d{4,})\b", re.I)


def extract_pronto_ids(text: str) -> list[str]:
    return [m.upper() for m in dict.fromkeys(_PRONTO_PATTERN.findall(text))]
