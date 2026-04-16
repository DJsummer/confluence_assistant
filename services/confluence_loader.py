"""
Confluence 数据同步模块（从 1.py 提炼为可复用服务）
支持：分页 / 递归 / 并发 / 增量同步
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import settings

log = logging.getLogger(__name__)

_PAGE_LIMIT = 50
_MAX_WORKERS = 8


# ─────────────────────── HTTP Session ───────────────────────────────────────

def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=_MAX_WORKERS + 4,
        pool_maxsize=_MAX_WORKERS + 4,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.auth = (settings.confluence_user, settings.confluence_token)
    return session


_SESSION = _make_session()


# ─────────────────────── API 调用 ───────────────────────────────────────────

def get_page_id(title: str, space: str) -> str:
    r = _SESSION.get(
        f"{settings.confluence_base}/rest/api/content",
        params={"title": title, "spaceKey": space, "limit": 1},
        timeout=30,
    )
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        raise ValueError(f"找不到页面：title={title!r}, space={space!r}")
    return results[0]["id"]


def _get_child_pages(page_id: str) -> list[dict]:
    """分页获取直接子页面（仅结构数据）。"""
    pages: list[dict] = []
    start = 0
    while True:
        r = _SESSION.get(
            f"{settings.confluence_base}/rest/api/content/{page_id}/child/page",
            params={"start": start, "limit": _PAGE_LIMIT},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        batch = data.get("results", [])
        if not batch:
            break
        pages.extend(batch)
        if "next" not in data.get("_links", {}):
            break
        start += _PAGE_LIMIT
    return pages


def _collect_tree(page_id: str, path: str) -> list[tuple[str, str]]:
    """深度优先递归收集整棵子树 (page_id, path) 节点列表。"""
    nodes: list[tuple[str, str]] = [(page_id, path)]
    for child in _get_child_pages(page_id):
        cid = child["id"]
        ctitle = child.get("title", cid)
        cpath = f"{path} > {ctitle}" if path else ctitle
        nodes.extend(_collect_tree(cid, cpath))
    return nodes


def _get_page_content(
    page_id: str,
    path: str = "",
    last_sync: Optional[datetime] = None,
) -> Optional[dict]:
    r = _SESSION.get(
        f"{settings.confluence_base}/rest/api/content/{page_id}",
        params={"expand": "body.storage,version"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()

    updated_at_str: str = data.get("version", {}).get("when", "")
    updated_at: Optional[datetime] = (
        datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
        if updated_at_str
        else None
    )

    if last_sync and updated_at and updated_at <= last_sync:
        log.debug("跳过（未变更）：%s [%s]", data.get("title"), page_id)
        return None

    html = data.get("body", {}).get("storage", {}).get("value", "")
    webui = data.get("_links", {}).get("webui", "")

    return {
        "id": page_id,
        "title": data.get("title", ""),
        "path": path,
        "content": _html_to_text(html),
        "url": f"{settings.confluence_base}{webui}" if webui else "",
        "updated_at": updated_at_str,
        "permissions": [],  # 可扩展：从 Confluence 权限 API 获取
    }


# ─────────────────────── HTML → 纯文本 ──────────────────────────────────────

def _table_to_text(table) -> str:
    """将 <table> 转为 Markdown 风格纯文本，保留行列结构。"""
    rows = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(separator=" ", strip=True) for td in tr.find_all(["th", "td"])]
        rows.append(" | ".join(cells))

    if not rows:
        return ""

    has_header = bool(table.find("th"))
    if has_header and len(rows) >= 1:
        col_count = len(table.find("tr").find_all(["th", "td"]))
        rows.insert(1, " | ".join(["---"] * col_count))

    return "\n".join(rows)


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for table in soup.find_all("table"):
        table.replace_with(soup.new_string("\n" + _table_to_text(table) + "\n"))
    return soup.get_text(separator="\n", strip=True)


# ─────────────────────── 主入口 ─────────────────────────────────────────────

def fetch_all_pages(
    root_title: str,
    space: str,
    last_sync: Optional[datetime] = None,
) -> list[dict]:
    log.info("查询根页面：title=%r  space=%r", root_title, space)
    root_id = get_page_id(root_title, space)

    log.info("遍历页面树结构…")
    id_path_list = _collect_tree(root_id, root_title)
    log.info("共发现 %d 个页面，开始并发获取内容…", len(id_path_list))

    pages: list[dict] = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {
            pool.submit(_get_page_content, pid, ppath, last_sync): pid
            for pid, ppath in id_path_list
        }
        for future in as_completed(futures):
            pid = futures[future]
            try:
                content = future.result()
                if content:
                    pages.append(content)
                    log.info("已获取：%s  [%s]", content["title"], pid)
            except Exception as exc:
                log.error("页面 %s 获取失败：%s", pid, exc)

    log.info("全部完成，共获取 %d 个页面", len(pages))
    return pages
