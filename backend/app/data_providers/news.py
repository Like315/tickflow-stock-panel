"""新闻数据源契约与 RSS/Atom 实现.

新闻和行情的时效、字段及失败语义不同, 因此不复用 MarketDataProvider.
RSS Provider 只保留公开元数据, 不下载或持久化新闻正文.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

_CHINA_TZ = ZoneInfo("Asia/Shanghai")
_MAX_FEED_BYTES = 1_000_000
_EASTMONEY_API_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_TITLE_KEY_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


@dataclass(frozen=True)
class NewsItem:
    """上层可依赖的最小新闻元数据。"""

    title: str
    published_at: datetime
    source: str
    source_url: str
    snippet: str = ""
    provider: str = ""

    def as_dict(self) -> dict[str, str]:
        published_at = self.published_at.astimezone(_CHINA_TZ)
        return {
            "title": self.title,
            "published_at": published_at.isoformat(timespec="seconds"),
            # 兼容 market_recap 已有的预注入字段名。
            "published_date": published_at.date().isoformat(),
            "source": self.source,
            "source_url": self.source_url,
            "snippet": self.snippet,
            "provider": self.provider,
        }


class NewsProvider(Protocol):
    name: str

    async def fetch_market(self, *, as_of: date, limit: int = 8) -> list[NewsItem]:
        """返回不晚于 as_of 的市场新闻, 按发布时间倒序."""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _clean_text(value: str | None, *, limit: int) -> str:
    text = _HTML_TAG_RE.sub(" ", html.unescape(value or ""))
    text = _CONTROL_CHARS_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()[:limit]


def _direct_child(node: ElementTree.Element, names: set[str]) -> ElementTree.Element | None:
    for child in node:
        if _local_name(child.tag) in names:
            return child
    return None


def _child_text(node: ElementTree.Element, names: set[str]) -> str:
    child = _direct_child(node, names)
    return "" if child is None else "".join(child.itertext())


def _parse_datetime(value: str, *, naive_tz=UTC) -> datetime | None:
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=naive_tz)
    return parsed.astimezone(_CHINA_TZ)


def _safe_http_url(value: str, *, base_url: str) -> str | None:
    candidate = urljoin(base_url, value.strip())
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return candidate


def _entry_url(node: ElementTree.Element, feed_url: str) -> str | None:
    fallback = ""
    for child in node:
        if _local_name(child.tag) != "link":
            continue
        candidate = child.attrib.get("href") or (child.text or "")
        rel = child.attrib.get("rel", "alternate")
        if rel in {"", "alternate"}:
            return _safe_http_url(candidate, base_url=feed_url)
        fallback = fallback or candidate
    guid = _child_text(node, {"guid", "id"})
    return _safe_http_url(fallback or guid, base_url=feed_url)


def _feed_title(root: ElementTree.Element) -> str:
    channel = _direct_child(root, {"channel"})
    parent = channel if channel is not None else root
    return _clean_text(_child_text(parent, {"title"}), limit=80)


def _entry_source(node: ElementTree.Element, fallback: str) -> str:
    source_node = _direct_child(node, {"source"})
    if source_node is None:
        return fallback
    nested_title = _child_text(source_node, {"title"})
    return _clean_text(nested_title or "".join(source_node.itertext()), limit=80) or fallback


def parse_rss_feed(
    payload: bytes,
    *,
    feed_url: str,
    as_of: date,
    lookback_days: int,
    provider_name: str,
) -> list[NewsItem]:
    """解析 RSS 2.0 / Atom 元数据, 并严格执行历史截止日."""
    upper_prefix = payload[:4096].upper()
    if b"<!DOCTYPE" in upper_prefix or b"<!ENTITY" in upper_prefix:
        raise ValueError("RSS XML 包含不安全的实体声明")
    root = ElementTree.fromstring(payload)
    fallback_source = _feed_title(root) or urlparse(feed_url).hostname or "RSS"
    earliest = as_of - timedelta(days=max(lookback_days - 1, 0))
    items: list[NewsItem] = []

    for node in root.iter():
        if _local_name(node.tag) not in {"item", "entry"}:
            continue
        title = _clean_text(_child_text(node, {"title"}), limit=200)
        published_at = _parse_datetime(
            _child_text(node, {"pubdate", "published", "updated", "date"})
        )
        source_url = _entry_url(node, feed_url)
        if not title or published_at is None or source_url is None:
            continue
        published_date = published_at.date()
        if published_date > as_of or published_date < earliest:
            continue
        snippet = _clean_text(
            _child_text(node, {"description", "summary", "content"}),
            limit=320,
        )
        items.append(NewsItem(
            title=title,
            published_at=published_at,
            source=_entry_source(node, fallback_source),
            source_url=source_url,
            snippet=snippet,
            provider=provider_name,
        ))
    return items


class RssNewsProvider:
    """从用户配置的多个 RSS/Atom 源批量拉取市场新闻。"""

    name = "rss"

    def __init__(
        self,
        feed_urls: Sequence[str],
        *,
        timeout: float = 6.0,
        cache_ttl: float = 600.0,
        lookback_days: int = 7,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.feed_urls = tuple(feed_urls)
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.lookback_days = lookback_days
        self._transport = transport
        self._cache_lock = threading.Lock()
        self._cache: dict[tuple[str, int], tuple[float, tuple[NewsItem, ...]]] = {}

    async def _download(self, client: httpx.AsyncClient, url: str) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            if response.url.scheme not in {"http", "https"}:
                raise ValueError("RSS 重定向到了不支持的协议")
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > _MAX_FEED_BYTES:
                    raise ValueError("RSS 响应超过 1 MB 上限")
                chunks.append(chunk)
        return b"".join(chunks)

    async def _fetch_one(
        self,
        client: httpx.AsyncClient,
        url: str,
        as_of: date,
    ) -> list[NewsItem]:
        payload = await self._download(client, url)
        return parse_rss_feed(
            payload,
            feed_url=url,
            as_of=as_of,
            lookback_days=self.lookback_days,
            provider_name=self.name,
        )

    async def fetch_market(self, *, as_of: date, limit: int = 8) -> list[NewsItem]:
        capped_limit = min(max(limit, 1), 30)
        cache_key = (as_of.isoformat(), capped_limit)
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] < self.cache_ttl:
                return list(cached[1])
        if not self.feed_urls:
            return []

        timeout = httpx.Timeout(self.timeout)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "TickFlow-Stock-Panel/1.0 RSS Reader"},
            transport=self._transport,
        ) as client:
            results = await asyncio.gather(
                *(self._fetch_one(client, url, as_of) for url in self.feed_urls),
                return_exceptions=True,
            )

        merged: list[NewsItem] = []
        for url, result in zip(self.feed_urls, results, strict=True):
            if isinstance(result, BaseException):
                host = urlparse(url).hostname or "unknown"
                logger.warning("RSS news source failed (%s): %s", host, type(result).__name__)
                continue
            merged.extend(result)

        merged.sort(key=lambda item: item.published_at, reverse=True)
        unique: list[NewsItem] = []
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()
        for item in merged:
            title_key = _TITLE_KEY_RE.sub("", item.title.casefold())
            if item.source_url in seen_urls or (title_key and title_key in seen_titles):
                continue
            seen_urls.add(item.source_url)
            if title_key:
                seen_titles.add(title_key)
            unique.append(item)
            if len(unique) >= capped_limit:
                break

        result_tuple = tuple(unique)
        with self._cache_lock:
            self._cache[cache_key] = (time.monotonic(), result_tuple)
        return list(result_tuple)


class EastmoneyNewsProvider:
    """东方财富全球财经快讯 Provider.

    接口是公开网页使用的快讯接口, 没有稳定性承诺; 任何异常均由聚合层降级.
    """

    name = "eastmoney"

    def __init__(
        self,
        *,
        timeout: float = 6.0,
        cache_ttl: float = 600.0,
        lookback_days: int = 2,
        max_pages: int = 5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.lookback_days = lookback_days
        self.max_pages = max_pages
        self._transport = transport
        self._cache_lock = threading.Lock()
        self._cache: dict[tuple[str, int], tuple[float, tuple[NewsItem, ...]]] = {}

    @staticmethod
    def _parse_rows(payload: object, *, as_of: date) -> list[NewsItem]:
        if not isinstance(payload, dict):
            raise ValueError("东方财富快讯响应格式异常")
        data = payload.get("data")
        rows = data.get("fastNewsList") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError("东方财富快讯列表缺失")

        items: list[NewsItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = _clean_text(str(row.get("title") or ""), limit=200)
            snippet = _clean_text(str(row.get("summary") or ""), limit=320)
            published_at = _parse_datetime(
                str(row.get("showTime") or ""),
                naive_tz=_CHINA_TZ,
            )
            code = str(row.get("code") or "").strip()
            if (
                not title
                or published_at is None
                or published_at.date() > as_of
                or not re.fullmatch(r"\d+", code)
            ):
                continue
            items.append(NewsItem(
                title=title,
                published_at=published_at,
                source="东方财富",
                source_url=f"https://finance.eastmoney.com/a/{code}.html",
                snippet=snippet,
                provider=EastmoneyNewsProvider.name,
            ))
        return items

    async def fetch_market(self, *, as_of: date, limit: int = 8) -> list[NewsItem]:
        capped_limit = min(max(limit, 1), 1000)
        cache_key = (as_of.isoformat(), capped_limit)
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] < self.cache_ttl:
                return list(cached[1])

        base_params = {
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "pageSize": "200",
            "req_trace": "1710315450384",
        }
        earliest = as_of - timedelta(days=max(self.lookback_days - 1, 0))
        items: list[NewsItem] = []
        cursor = ""
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 TickFlow-Stock-Panel/1.0"},
            transport=self._transport,
        ) as client:
            for page_index in range(max(self.max_pages, 1)):
                try:
                    response = await client.get(
                        _EASTMONEY_API_URL,
                        params={**base_params, "sortEnd": cursor},
                    )
                    response.raise_for_status()
                    if len(response.content) > _MAX_FEED_BYTES:
                        raise ValueError("东方财富快讯响应超过 1 MB 上限")
                    payload = response.json()
                    page_items = self._parse_rows(payload, as_of=as_of)
                except Exception:
                    if page_index == 0:
                        raise
                    logger.warning("Eastmoney news pagination stopped at page %d", page_index + 1)
                    break

                items.extend(page_items)
                data = payload.get("data") if isinstance(payload, dict) else None
                rows = data.get("fastNewsList") if isinstance(data, dict) else None
                if not isinstance(rows, list) or not rows:
                    break
                last_row = rows[-1] if isinstance(rows[-1], dict) else {}
                next_cursor = str(last_row.get("realSort") or "").strip()
                oldest_at = _parse_datetime(
                    str(last_row.get("showTime") or ""),
                    naive_tz=_CHINA_TZ,
                )
                if (
                    not next_cursor
                    or next_cursor == cursor
                    or (oldest_at is not None and oldest_at.date() < earliest)
                ):
                    break
                cursor = next_cursor

        items = [item for item in items if item.published_at.date() >= earliest]
        items.sort(key=lambda item: item.published_at, reverse=True)
        result_tuple = tuple(items[:capped_limit])
        with self._cache_lock:
            self._cache[cache_key] = (time.monotonic(), result_tuple)
        return list(result_tuple)
