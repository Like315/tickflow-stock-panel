"""巨潮资讯公告元数据 Provider。

只读取官网全文检索返回的标题、日期和原文链接；失败时返回明确降级状态。
"""

from __future__ import annotations

import html
import re
import threading
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

_API_URL = "https://www.cninfo.com.cn/new/fulltextSearch/full"
_SEARCH_URL = "https://www.cninfo.com.cn/new/fulltextSearch?keyWord={code}"
_STATIC_BASE = "https://static.cninfo.com.cn/"
_ALLOWED_HOSTS = {"www.cninfo.com.cn", "static.cninfo.com.cn"}
_CACHE_TTL_SECONDS = 1800


def _clean_title(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def _safe_cninfo_url(path: str) -> str | None:
    candidate = path.strip()
    if not candidate:
        return None
    parsed_candidate = urlparse(candidate)
    if parsed_candidate.scheme and parsed_candidate.scheme != "https":
        return None
    url = candidate if parsed_candidate.scheme else _STATIC_BASE + candidate.lstrip("/")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        return None
    return url


class CninfoAnnouncementProvider:
    def __init__(self, *, timeout: float = 8.0) -> None:
        self.timeout = timeout
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}

    def fetch(
        self, symbol: str, *, end_date: date, days: int = 45, limit: int = 5
    ) -> dict[str, Any]:
        code = symbol.split(".", 1)[0]
        key = (code, end_date.isoformat())
        with self._lock:
            cached = self._cache.get(key)
            if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
                return cached[1]
        start_date = end_date - timedelta(days=max(days, 1))
        search_url = _SEARCH_URL.format(code=code)
        try:
            response = httpx.get(
                _API_URL,
                params={
                    "searchkey": code,
                    "sdate": start_date.isoformat(),
                    "edate": end_date.isoformat(),
                    "isfulltext": "false",
                    "sortName": "nothing",
                    "sortType": "desc",
                    "pageNum": 1,
                },
                headers={
                    "Referer": search_url,
                    "User-Agent": "TickFlow-Stock-Panel/1.0",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=self.timeout,
                follow_redirects=False,
            )
            response.raise_for_status()
            data = response.json()
            rows = data.get("announcements") if isinstance(data, dict) else None
            if rows is None:
                rows = []
            if not isinstance(rows, list):
                raise ValueError("公告响应字段格式异常")
            announcements = []
            for row in rows:
                if not isinstance(row, dict) or str(row.get("secCode") or "") != code:
                    continue
                source_url = _safe_cninfo_url(str(row.get("adjunctUrl") or ""))
                if not source_url:
                    continue
                timestamp = row.get("announcementTime")
                if not isinstance(timestamp, (int, float)):
                    continue
                published_date = datetime.fromtimestamp(timestamp / 1000, tz=UTC).date()
                if published_date > end_date:
                    continue
                published_at = published_date.isoformat()
                announcements.append(
                    {
                        "title": _clean_title(str(row.get("announcementTitle") or ""))[:240],
                        "published_at": published_at,
                        "url": source_url,
                        "source": "巨潮资讯网",
                    }
                )
                if len(announcements) >= min(max(limit, 1), 10):
                    break
            result = {
                "available": True,
                "source": "巨潮资讯网",
                "search_url": search_url,
                "announcements": announcements,
                "news": [],
                "message": None if announcements else "查询期内未发现公告",
            }
        except Exception as exc:
            result = {
                "available": False,
                "source": "巨潮资讯网",
                "search_url": search_url,
                "announcements": [],
                "news": [],
                "message": f"公告补充暂不可用: {type(exc).__name__}",
            }
        with self._lock:
            self._cache[key] = (time.monotonic(), result)
        return result


class EmptyNewsProvider:
    def fetch(self, _symbol: str, *, end_date: date) -> dict[str, Any]:
        del end_date
        return {"available": False, "news": [], "message": "普通新闻 Provider 尚未配置"}
