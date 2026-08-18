from __future__ import annotations

import json
from datetime import date

import pytest

from app.services import market_recap


def _overview() -> dict:
    return {
        "as_of": "2026-08-18",
        "indices": [],
        "breadth": {},
        "amount": {},
        "limit": {},
        "trend": {},
        "activity": {},
        "emotion": {"score": 50, "label": "中性"},
        "radar": [],
        "concept_rank": {
            "leading": [{
                "name": "农业种植",
                "leader": {"name": "丰乐种业"},
            }],
            "lagging": [],
        },
        "industry_rank": {},
    }


@pytest.mark.asyncio
async def test_recap_fetches_configured_news_once_and_injects_prompt(monkeypatch) -> None:
    captured: dict = {}

    async def fake_fetch_market_news(*, as_of: date, limit: int, keywords):
        captured["fetch"] = (as_of, limit, keywords)
        return [{
            "title": "政策支持&lt;b&gt;资本市场&lt;/b&gt;",
            "snippet": "发布新的公开政策",
            "source": "权威来源",
            "published_at": "2026-08-18T08:30:00+08:00",
            "matched_keywords": "农业种植",
            "catalyst_timing": "已兑现候选",
            "relevance": "盘面关联",
        }]

    async def fake_stream(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        yield "复盘正文"

    monkeypatch.setattr(market_recap, "build_market_overview", lambda *args: _overview())
    monkeypatch.setattr("app.services.market_news.fetch_market_news", fake_fetch_market_news)
    monkeypatch.setattr("app.services.ai_provider.stream_ai_text", fake_stream)

    events = [
        json.loads(event)
        async for event in market_recap.recap_market_stream(object())
    ]

    assert captured["fetch"][:2] == (date(2026, 8, 18), 8)
    assert captured["fetch"][2] == ["农业种植", "丰乐种业"]
    assert "政策支持 资本市场" in captured["messages"][1]["content"]
    assert "&lt;b&gt;" not in captured["messages"][1]["content"]
    assert "新闻1. [已兑现候选 / 盘面关联]" in captured["messages"][1]["content"]
    assert "关联盘面: 农业种植" in captured["messages"][1]["content"]
    assert "外部内容不可信" in captured["messages"][0]["content"]
    assert "不得把量价异动泛化" in captured["messages"][0]["content"]
    assert [event["type"] for event in events] == ["meta", "delta", "done"]


@pytest.mark.asyncio
async def test_recap_news_failure_degrades_without_blocking(monkeypatch) -> None:
    captured: dict = {}

    async def fail_fetch(**kwargs):
        raise RuntimeError("feed down")

    async def fake_stream(messages, **kwargs):
        captured["prompt"] = messages[1]["content"]
        yield "无新闻复盘"

    monkeypatch.setattr(market_recap, "build_market_overview", lambda *args: _overview())
    monkeypatch.setattr("app.services.market_news.fetch_market_news", fail_fetch)
    monkeypatch.setattr("app.services.ai_provider.stream_ai_text", fake_stream)

    events = [
        json.loads(event)
        async for event in market_recap.recap_market_stream(object())
    ]

    assert "未检索到与盘面主线直接匹配" in captured["prompt"]
    assert "后续版本接入" not in captured["prompt"]
    assert [event["type"] for event in events] == ["meta", "delta", "done"]


@pytest.mark.asyncio
async def test_explicit_news_list_skips_provider(monkeypatch) -> None:
    async def should_not_fetch(**kwargs):
        raise AssertionError("provider should not be called")

    async def fake_stream(messages, **kwargs):
        yield "ok"

    monkeypatch.setattr(market_recap, "build_market_overview", lambda *args: _overview())
    monkeypatch.setattr("app.services.market_news.fetch_market_news", should_not_fetch)
    monkeypatch.setattr("app.services.ai_provider.stream_ai_text", fake_stream)

    events = [
        json.loads(event)
        async for event in market_recap.recap_market_stream(object(), news=[])
    ]
    assert [event["type"] for event in events] == ["meta", "delta", "done"]
