from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.market_time import CN_TZ
from app.services.news_sentiment import NewsSentimentService, score_candidate_news


def _row(
    title: str,
    published_at: datetime,
    *,
    source: str = "测试源",
    snippet: str = "",
) -> dict[str, str]:
    return {
        "title": title,
        "published_at": published_at.isoformat(),
        "source": source,
        "source_url": f"https://example.com/{abs(hash(title))}",
        "snippet": snippet,
        "provider": "test",
    }


def test_news_sentiment_combines_global_domestic_and_intraday_news() -> None:
    now = datetime(2026, 8, 20, 10, 0, tzinfo=CN_TZ)

    async def fetcher(**_kwargs):
        return [
            _row("美联储降息预期升温,美股上涨", now - timedelta(hours=10)),
            _row("国务院发布产业支持政策", now - timedelta(hours=2)),
            _row("盘中龙头公司获得重大订单并大涨", now - timedelta(minutes=15)),
            _row("未来消息不应进入评分", now + timedelta(hours=1)),
        ]

    context = NewsSentimentService(fetcher=fetcher).get_context(now)

    assert context["available"] is True
    assert context["score"] > 0
    assert context["item_count"] == 3
    assert context["signal_count"] == 3
    assert context["regions"] == {"global": 1, "domestic": 1, "market": 1}


def test_candidate_news_score_uses_company_and_industry_relevance() -> None:
    now = datetime(2026, 8, 20, 10, 0, tzinfo=CN_TZ)
    context = NewsSentimentService._build_context(
        [
            _row("宁德时代获大额订单,盈利增长", now - timedelta(minutes=20)),
            _row("半导体行业遭遇出口禁令", now - timedelta(minutes=10)),
            _row("300750发布回购方案", now - timedelta(minutes=5)),
        ],
        now,
    )

    scored = score_candidate_news(context, {
        "SZ.300750": {"name": "宁德时代"},
        "SH.688001": {"name": "华兴公司", "industry": "半导体"},
        "SH.600000": {"name": "浦发银行", "industry": "银行"},
    })

    assert scored["SZ.300750"]["score"] > 0
    assert scored["SZ.300750"]["matched_count"] == 2
    assert scored["SH.688001"]["score"] < 0
    assert scored["SH.600000"]["score"] == 0


def test_news_sentiment_failure_degrades_to_neutral() -> None:
    now = datetime(2026, 8, 20, 9, 15, tzinfo=CN_TZ)

    async def fetcher(**_kwargs):
        raise RuntimeError("news unavailable")

    context = NewsSentimentService(fetcher=fetcher).get_context(now)

    assert context["available"] is False
    assert context["status"] == "unavailable"
    assert context["score"] == pytest.approx(0.0)
