from __future__ import annotations

from datetime import date, datetime

import httpx
import pytest

from app.data_providers.news import EastmoneyNewsProvider, NewsItem, RssNewsProvider, parse_rss_feed
from app.services import market_news
from app.services.market_news import parse_rss_urls

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>测试财经</title>
    <item>
      <title><![CDATA[政策支持 <b>资本市场</b>]]></title>
      <link>/news/1</link>
      <pubDate>Tue, 18 Aug 2026 08:30:00 +0800</pubDate>
      <description><![CDATA[<p>摘要一</p>]]></description>
    </item>
    <item>
      <title>未来消息</title>
      <link>https://news.example/future</link>
      <pubDate>Wed, 19 Aug 2026 08:30:00 +0800</pubDate>
    </item>
    <item>
      <title>无日期消息</title>
      <link>https://news.example/no-date</link>
    </item>
  </channel>
</rss>""".encode()

ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>另一个来源</title>
  <entry>
    <title>外围市场收涨</title>
    <link href="https://atom.example/2" rel="alternate" />
    <updated>2026-08-17T23:30:00Z</updated>
    <summary>海外市场摘要</summary>
  </entry>
</feed>""".encode()


def test_parse_rss_filters_future_and_requires_publication_time() -> None:
    items = parse_rss_feed(
        RSS_XML,
        feed_url="https://news.example/feed.xml",
        as_of=date(2026, 8, 18),
        lookback_days=7,
        provider_name="rss",
    )

    assert len(items) == 1
    assert items[0].title == "政策支持 资本市场"
    assert items[0].source == "测试财经"
    assert items[0].source_url == "https://news.example/news/1"
    assert items[0].snippet == "摘要一"
    assert items[0].as_dict()["published_date"] == "2026-08-18"


@pytest.mark.asyncio
async def test_provider_merges_partial_results_dedupes_and_caches() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "bad.example":
            return httpx.Response(503, request=request)
        payload = RSS_XML if request.url.host == "news.example" else ATOM_XML
        return httpx.Response(200, content=payload, request=request)

    provider = RssNewsProvider(
        [
            "https://news.example/feed.xml",
            "https://atom.example/feed.xml",
            "https://bad.example/feed.xml",
        ],
        transport=httpx.MockTransport(handler),
    )

    first = await provider.fetch_market(as_of=date(2026, 8, 18), limit=8)
    second = await provider.fetch_market(as_of=date(2026, 8, 18), limit=8)

    assert [item.title for item in first] == ["政策支持 资本市场", "外围市场收涨"]
    assert second == first
    assert len(calls) == 3


def test_parse_rss_urls_rejects_unsafe_or_credential_urls() -> None:
    urls = parse_rss_urls(
        "https://one.example/feed;ftp://bad.example/x;"
        "https://user:pass@two.example/feed\nhttp://three.example/rss"
    )
    assert urls == ("https://one.example/feed", "http://three.example/rss")


@pytest.mark.asyncio
async def test_eastmoney_provider_filters_future_rows_and_caches() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "data": {
                    "fastNewsList": [
                        {
                            "title": "当日市场快讯",
                            "summary": "公开摘要",
                            "showTime": "2026-08-18 16:30:00",
                            "code": "202608183500001",
                        },
                        {
                            "title": "未来快讯",
                            "summary": "不可用于历史复盘",
                            "showTime": "2026-08-19 09:00:00",
                            "code": "202608193500002",
                        },
                    ]
                }
            },
            request=request,
        )

    provider = EastmoneyNewsProvider(transport=httpx.MockTransport(handler))
    first = await provider.fetch_market(as_of=date(2026, 8, 18), limit=8)
    second = await provider.fetch_market(as_of=date(2026, 8, 18), limit=8)

    assert [item.title for item in first] == ["当日市场快讯"]
    assert first[0].published_at.isoformat() == "2026-08-18T16:30:00+08:00"
    assert first[0].source_url == "https://finance.eastmoney.com/a/202608183500001.html"
    assert second == first
    assert calls == 1


@pytest.mark.asyncio
async def test_eastmoney_provider_paginates_until_recap_window_is_covered() -> None:
    cursors: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("sortEnd", "")
        cursors.append(cursor)
        if not cursor:
            rows = [
                {
                    "title": "收盘后消息",
                    "summary": "等待后续交易日验证",
                    "showTime": "2026-08-18 20:30:00",
                    "code": "202608183500010",
                    "realSort": "cursor-1",
                }
            ]
        else:
            rows = [
                {
                    "title": "早盘农业政策消息",
                    "summary": "种植产业相关公开消息",
                    "showTime": "2026-08-18 09:10:00",
                    "code": "202608183500011",
                    "realSort": "cursor-2",
                },
                {
                    "title": "窗口外旧消息",
                    "summary": "不应进入结果",
                    "showTime": "2026-08-16 18:00:00",
                    "code": "202608163500012",
                    "realSort": "cursor-3",
                },
            ]
        return httpx.Response(
            200,
            json={"data": {"fastNewsList": rows}},
            request=request,
        )

    provider = EastmoneyNewsProvider(transport=httpx.MockTransport(handler))
    items = await provider.fetch_market(as_of=date(2026, 8, 18), limit=20)

    assert cursors == ["", "cursor-1"]
    assert [item.title for item in items] == ["收盘后消息", "早盘农业政策消息"]


@pytest.mark.asyncio
async def test_market_news_aggregator_keeps_healthy_provider(monkeypatch) -> None:
    item = NewsItem(
        title="有效快讯",
        published_at=datetime.fromisoformat("2026-08-18T16:30:00+08:00"),
        source="测试来源",
        source_url="https://news.example/1",
        provider="healthy",
    )

    class FailingProvider:
        name = "failing"

        async def fetch_market(self, **kwargs):
            raise httpx.TimeoutException("timeout")

    class HealthyProvider:
        name = "healthy"

        async def fetch_market(self, **kwargs):
            return [item]

    monkeypatch.setattr(
        market_news,
        "_get_providers",
        lambda: [FailingProvider(), HealthyProvider()],
    )

    result = await market_news.fetch_market_news(as_of=date(2026, 8, 18), limit=8)
    assert [row["title"] for row in result] == ["有效快讯"]


@pytest.mark.asyncio
async def test_market_news_ranks_sector_matches_before_unrelated_latest(monkeypatch) -> None:
    def item(title: str, published_at: str, snippet: str = "") -> NewsItem:
        return NewsItem(
            title=title,
            published_at=datetime.fromisoformat(published_at),
            source="测试来源",
            source_url=f"https://news.example/{published_at.replace(':', '')}",
            snippet=snippet,
            provider="test",
        )

    rows = [
        item("电竞赛事最新消息", "2026-08-18T20:30:00+08:00"),
        item("丰乐种业披露重要进展", "2026-08-18T16:30:00+08:00"),
        item("农业种植支持政策发布", "2026-08-18T09:10:00+08:00"),
        item("央行公布最新货币政策", "2026-08-18T14:00:00+08:00"),
    ]

    class Provider:
        name = "test"

        async def fetch_market(self, **kwargs):
            assert kwargs["limit"] == 800
            return rows

    monkeypatch.setattr(market_news, "_get_providers", lambda: [Provider()])
    result = await market_news.fetch_market_news(
        as_of=date(2026, 8, 18),
        limit=3,
        keywords=["农业种植", "丰乐种业"],
    )

    assert [row["title"] for row in result] == [
        "丰乐种业披露重要进展",
        "农业种植支持政策发布",
        "央行公布最新货币政策",
    ]
    assert result[0]["catalyst_timing"] == "待发酵"
    assert result[1]["catalyst_timing"] == "已兑现候选"
    assert result[1]["matched_keywords"] == "农业种植"
    assert all(row["title"] != "电竞赛事最新消息" for row in result)


def test_ambiguous_industry_keyword_does_not_match_company_suffix_only() -> None:
    company_news = NewsItem(
        title="美银证券维持海外科技公司买入评级",
        published_at=datetime.fromisoformat("2026-08-18T16:00:00+08:00"),
        source="测试来源",
        source_url="https://news.example/company",
    )
    sector_news = NewsItem(
        title="券商板块午后走强",
        published_at=datetime.fromisoformat("2026-08-18T14:00:00+08:00"),
        source="测试来源",
        source_url="https://news.example/sector",
    )

    ranked = market_news._rank_news(
        [company_news, sector_news],
        keywords=["证券"],
        as_of=date(2026, 8, 18),
        limit=8,
    )
    assert [row["title"] for row in ranked] == ["券商板块午后走强"]
