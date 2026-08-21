"""市场新闻编排: 读取配置、解析 RSS 地址并提供失败降级."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections.abc import Sequence
from datetime import date
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from app.config import settings
from app.data_providers.news import EastmoneyNewsProvider, NewsItem, NewsProvider, RssNewsProvider

logger = logging.getLogger(__name__)

_PROVIDER_LOCK = threading.Lock()
_PROVIDER_KEY: tuple[str, ...] | None = None
_PROVIDER: RssNewsProvider | None = None
_EASTMONEY_PROVIDER = EastmoneyNewsProvider()
_CHINA_TZ = ZoneInfo("Asia/Shanghai")
_GENERIC_KEYWORDS = {
    "A股",
    "AI",
    "中国",
    "产业",
    "公司",
    "市场",
    "指数",
    "概念",
    "板块",
    "股票",
    "科技",
    "行业",
    "集团",
    "龙头",
}
_MACRO_TERMS = (
    "国务院",
    "央行",
    "证监会",
    "金融监管总局",
    "财政部",
    "商务部",
    "发改委",
    "货币政策",
    "财政政策",
    "降准",
    "降息",
    "利率",
    "关税",
    "人民币",
    "美联储",
    "CPI",
    "PPI",
    "GDP",
    "通胀",
    "就业",
    "进口价格",
    "新屋开工",
)
_AMBIGUOUS_KEYWORD_CONTEXT = {
    "证券": ("券商", "证券板块", "证券行业", "证券公司", "经纪业务", "投行业务", "证监会"),
    "银行": ("银行板块", "银行业", "商业银行", "央行", "净息差", "存款利率", "贷款利率"),
    "保险": ("保险板块", "保险业", "保险公司", "保费", "险资"),
}


def parse_rss_urls(raw: str, *, max_feeds: int = 10) -> tuple[str, ...]:
    """解析分号或换行分隔的 RSS 地址; 非法地址 fail-closed."""
    urls: list[str] = []
    for value in re.split(r"[;\r\n]+", raw or ""):
        candidate = value.strip()
        if not candidate:
            continue
        parsed = urlparse(candidate)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            logger.warning("ignored invalid RSS URL")
            continue
        if candidate not in urls:
            urls.append(candidate)
        if len(urls) >= max_feeds:
            break
    return tuple(urls)


def _get_providers() -> list[NewsProvider]:
    global _PROVIDER, _PROVIDER_KEY
    providers: list[NewsProvider] = []
    if settings.news_eastmoney_enabled:
        providers.append(_EASTMONEY_PROVIDER)
    urls = parse_rss_urls(settings.news_rss_urls)
    if not urls:
        return providers
    with _PROVIDER_LOCK:
        if _PROVIDER is None or urls != _PROVIDER_KEY:
            _PROVIDER = RssNewsProvider(urls)
            _PROVIDER_KEY = urls
        providers.append(_PROVIDER)
    return providers


def _keyword_variants(value: str) -> tuple[str, ...]:
    keyword = re.sub(r"\s+", "", value).strip()
    if len(keyword) < 2 or keyword in _GENERIC_KEYWORDS:
        return ()
    variants = [keyword]
    for alias in _AMBIGUOUS_KEYWORD_CONTEXT.get(keyword, ()):
        if alias not in variants:
            variants.append(alias)
    for part in re.split(r"[-_/、·]+", keyword):
        if len(part) >= 2 and part not in _GENERIC_KEYWORDS and part not in variants:
            variants.append(part)
    if re.fullmatch(r"[\u4e00-\u9fff]{4,}", keyword):
        for index in range(len(keyword) - 1):
            part = keyword[index : index + 2]
            if part not in _GENERIC_KEYWORDS and part not in variants:
                variants.append(part)
    return tuple(variants)


def _item_relevance(item: NewsItem, keywords: Sequence[str]) -> tuple[int, list[str]]:
    title = item.title.casefold()
    body = f"{item.title} {item.snippet}".casefold()
    exact_score = 0
    fuzzy_score = 0
    exact_matched: list[str] = []
    fuzzy_matched: list[str] = []
    for raw_keyword in keywords:
        keyword = str(raw_keyword or "").strip()
        variants = _keyword_variants(keyword)
        if not variants:
            continue
        normalized = variants[0].casefold()
        required_context = _AMBIGUOUS_KEYWORD_CONTEXT.get(keyword)
        has_required_context = not required_context or any(
            context.casefold() in body for context in required_context
        )
        if normalized in title and has_required_context:
            exact_score += 12
            exact_matched.append(keyword)
        elif normalized in body and has_required_context:
            exact_score += 7
            exact_matched.append(keyword)
        else:
            hits = sum(1 for variant in variants[1:] if variant.casefold() in body)
            if hits:
                fuzzy_score += min(hits * 2, 6)
                fuzzy_matched.append(keyword)
    if exact_matched:
        return exact_score, exact_matched
    return fuzzy_score, fuzzy_matched


def _catalyst_timing(item: NewsItem, as_of: date) -> str:
    published_at = item.published_at.astimezone(_CHINA_TZ)
    if published_at.date() < as_of or published_at.hour < 15:
        return "已兑现候选"
    return "待发酵"


def _rank_news(
    items: Sequence[NewsItem],
    *,
    keywords: Sequence[str],
    as_of: date,
    limit: int,
) -> list[dict[str, str]]:
    scored: list[tuple[int, int, float, NewsItem, list[str], str]] = []
    for item in items:
        score, matched = _item_relevance(item, keywords)
        relevance = "盘面关联"
        tier = 2
        if not matched:
            macro_hits = [
                term
                for term in _MACRO_TERMS
                if term.casefold() in f"{item.title} {item.snippet}".casefold()
            ]
            if not macro_hits:
                continue
            matched = ["宏观/监管"]
            score = min(len(macro_hits) * 2, 6)
            relevance = "宏观背景"
            tier = 1
        scored.append(
            (
                tier,
                score,
                item.published_at.timestamp(),
                item,
                matched,
                relevance,
            )
        )

    scored.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    selected: list[dict[str, str]] = []
    for _, _, _, item, matched, relevance in scored[:limit]:
        row = item.as_dict()
        row["matched_keywords"] = "、".join(dict.fromkeys(matched[:4]))
        row["catalyst_timing"] = _catalyst_timing(item, as_of)
        row["relevance"] = relevance
        selected.append(row)
    return selected


async def fetch_market_news(
    *,
    as_of: date,
    limit: int = 8,
    keywords: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    """获取并按盘面关键词筛选市场新闻; 来源失败时明确降级."""
    providers = _get_providers()
    if not providers:
        return []
    capped_limit = min(max(limit, 1), 120)
    provider_limit = 800 if keywords else capped_limit
    results = await asyncio.gather(
        *(provider.fetch_market(as_of=as_of, limit=provider_limit) for provider in providers),
        return_exceptions=True,
    )
    merged: list[NewsItem] = []
    for provider, result in zip(providers, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning(
                "market news provider failed (%s): %s",
                provider.name,
                type(result).__name__,
            )
            continue
        merged.extend(result)

    merged.sort(key=lambda item: item.published_at, reverse=True)
    unique: list[NewsItem] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for item in merged:
        title_key = re.sub(r"[^\w\u4e00-\u9fff]+", "", item.title.casefold())
        if item.source_url in seen_urls or (title_key and title_key in seen_titles):
            continue
        seen_urls.add(item.source_url)
        if title_key:
            seen_titles.add(title_key)
        unique.append(item)
    if keywords:
        return _rank_news(unique, keywords=keywords, as_of=as_of, limit=capped_limit)
    return [item.as_dict() for item in unique[:capped_limit]]
