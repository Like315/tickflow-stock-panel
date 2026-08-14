from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl
import pytest

from app.services.fund_market_research import (
    FUND_UNIVERSE,
    FundMarketResearchService,
    _classify,
    _market_regime,
)


def _history(trend: str, days: int = 400, start: date | None = None) -> list[dict]:
    start = start or date(2025, 7, 1)
    rows = []
    for index in range(days):
        current = start + timedelta(days=index)
        if trend == "up":
            nav = 1.0 + index * 0.002
        elif trend == "down":
            nav = 2.0 - index * 0.002
        else:
            nav = 1.0 + 0.1 * math.sin(index / 7)
        rows.append({"date": current.isoformat(), "nav": round(nav, 4)})
    return rows


def _snapshot(
    code: str,
    name: str,
    *,
    performance: dict,
    volatility: float = 18.0,
    max_drawdown: float = -12.0,
    positive_ratio: float = 52.0,
    sample_days: int = 400,
    trend: str = "flat",
    category: str = "主动权益",
) -> dict:
    return {
        "code": code,
        "name": name,
        "fund_type": "混合型-偏股" if category != "债券" else "债券型",
        "company": "示例基金公司",
        "managers": ["基金经理甲"],
        "nav_as_of": "2026-08-12",
        "asset_allocation": None,
        "performance_pct": performance,
        "annualized_volatility_pct": volatility,
        "max_drawdown_1y_pct": max_drawdown,
        "positive_day_ratio_pct": positive_ratio,
        "sample_days": sample_days,
        "source": "fake_public_fund_nav",
        "_trend": trend,
        "_category": category,
    }


STRONG = _snapshot("005827", "易方达蓝筹精选混合", performance={"1m": 3.0, "3m": 6.0, "6m": 12.0, "1y": 20.0}, volatility=20.0, max_drawdown=-10.0, positive_ratio=56.0, trend="up")
WEAK = _snapshot("161725", "招商中证白酒指数A", performance={"1m": -2.0, "3m": -6.0, "6m": -10.0, "1y": -5.0}, volatility=28.0, max_drawdown=-22.0, positive_ratio=42.0, trend="down")
DEFAULT = _snapshot("110020", "易方达沪深300ETF联接A", performance={"1m": 0.5, "3m": 1.0, "6m": 2.0, "1y": 4.0}, volatility=15.0, max_drawdown=-8.0, positive_ratio=52.0, trend="up", category="宽基指数")


class FakeMarketProvider:
    def __init__(self, snapshots: dict[str, dict]) -> None:
        self._snapshots = snapshots

    def market_snapshot(self, code: str, *, include_history: bool = False) -> dict:
        try:
            snapshot = self._snapshots[code]
        except KeyError as exc:
            raise ValueError(f"没有查询到基金 {code}") from exc
        result = {key: value for key, value in snapshot.items() if not key.startswith("_")}
        if include_history:
            result["history"] = _history(snapshot["_trend"])
        return result

    def close(self) -> None:
        return None


class FakeRepo:
    def get_index_daily(self, symbol, start, end, columns=None):
        dates = [date(2025, 1, 1) + timedelta(days=index) for index in range(600)]
        closes = [100.0 * (1 + index * 0.0005) for index in range(600)]
        frame = pl.DataFrame({"symbol": symbol, "date": dates, "close": closes})
        return frame.select(columns or ["symbol", "date", "close"])


def _full_provider() -> FakeMarketProvider:
    snapshots = {}
    for item in FUND_UNIVERSE:
        if item["code"] == "005827":
            snapshots[item["code"]] = STRONG
        elif item["code"] == "161725":
            snapshots[item["code"]] = WEAK
        else:
            snapshots[item["code"]] = dict(DEFAULT, code=item["code"], name=item["name"], _category=item["category"])
    return FakeMarketProvider(snapshots)


def test_market_regime_labels() -> None:
    up = {"symbol": "000300.SH", "trend": "上行", "return_20d_pct": 2.5, "as_of": "2026-08-12"}
    assert _market_regime([up])["regime"] == "上行"
    down = {"symbol": "000300.SH", "trend": "下行", "return_20d_pct": -3.0, "as_of": "2026-08-12"}
    assert _market_regime([down])["regime"] == "下行"
    mixed = {"symbol": "000300.SH", "trend": "震荡或方向不明", "return_20d_pct": 0.4, "as_of": "2026-08-12"}
    assert _market_regime([mixed])["regime"] == "震荡"
    assert _market_regime([])["regime"] == "未知"


def test_classify_buy_and_hold_and_reduce_and_watch() -> None:
    strong = dict(DEFAULT)
    strong["performance_pct"] = {"1m": 3.0, "3m": 6.0, "6m": 12.0, "1y": 20.0}
    strong["alpha_6m_pct"] = 8.0
    strong["alpha_1y_pct"] = 10.0
    assert _classify(strong, "上行")["tier"] == "可买入"
    # 下行环境下不允许新增买入 → 优质基金落到长期持有
    assert _classify(strong, "下行")["tier"] == "长期持有"

    weak = dict(DEFAULT)
    weak["performance_pct"] = {"1m": -2.0, "3m": -6.0, "6m": -10.0, "1y": -5.0}
    weak["alpha_6m_pct"] = -12.0
    assert _classify(weak, "上行")["tier"] == "减仓"

    insufficient = dict(DEFAULT, sample_days=60)
    assert _classify(insufficient, "上行")["tier"] == "观望"

    neutral = dict(DEFAULT)
    neutral["performance_pct"] = {"1m": -1.0, "3m": 1.5, "6m": -0.5, "1y": -2.0}
    assert _classify(neutral, "震荡")["tier"] == "观望"


def test_run_research_sorts_and_classifies_without_positions() -> None:
    service = FundMarketResearchService(FakeRepo(), market_provider=_full_provider())
    try:
        result = service.run_research()
    finally:
        service.close()

    assert result["scope"] == "fund_market"
    assert result["market_regime"]["regime"] == "上行"
    funds = {str(fund["code"]): fund for fund in result["funds"]}
    assert funds["005827"]["recommendation"]["tier"] == "可买入"
    assert funds["161725"]["recommendation"]["tier"] == "减仓"
    # 买入名单受类别分散约束，上限 5 只
    assert result["summary"]["可买入"] == 5
    assert result["summary"]["减仓"] == 1
    # 基准数据可用时每个基金都应带上超额收益与相关性
    assert funds["005827"]["alpha_6m_pct"] is not None
    assert funds["005827"]["correlation_6m"] is not None
    assert funds["005827"]["beta_6m"] is not None
    assert all(fund.get("score") is not None for fund in result["funds"])
    assert result["benchmark"]["symbol"] == "000300.SH"


def test_run_research_with_custom_codes_and_invalid_input() -> None:
    provider = _full_provider()
    provider._snapshots["000171"] = _snapshot(
        "000171", "易方达裕丰回报债券", performance={"1m": 0.2, "3m": 0.4, "6m": -0.5, "1y": 4.0},
        volatility=4.0, max_drawdown=-1.0, positive_ratio=55.0, trend="up", category="债券",
    )
    service = FundMarketResearchService(FakeRepo(), market_provider=provider)
    try:
        result = service.run_research(codes=["000171"])
        assert result["universe_count"] == 1
        assert result["funds"][0]["code"] == "000171"
        assert result["funds"][0]["recommendation"]["tier"] == "长期持有"
        with pytest.raises(ValueError, match="6 位数字"):
            service.run_research(codes=["12345"])
    finally:
        service.close()


def test_run_research_reports_data_gap_for_missing_fund() -> None:
    service = FundMarketResearchService(
        FakeRepo(),
        market_provider=FakeMarketProvider({"110020": DEFAULT}),
    )
    try:
        result = service.run_research(codes=["110020", "999999"])
    finally:
        service.close()
    assert result["universe_count"] == 1
    assert any("999999" in gap for gap in result["data_gaps"])


def test_run_research_marks_held_funds_without_consuming_buy_cap() -> None:
    service = FundMarketResearchService(
        FakeRepo(),
        market_provider=_full_provider(),
    )
    try:
        result = service.run_research(held_codes=["005827"])
    finally:
        service.close()
    funds = {str(fund["code"]): fund for fund in result["funds"]}
    assert funds["005827"]["held"] is True
    # 持有的可买入基金保留原档位（加仓候选），不占用外部买入名额
    assert funds["005827"]["recommendation"]["tier"] == "可买入"
    assert result["held_count"] == 1
    # 外部市场买入名单仍被截断到 5 只：外部 5 + 持有 1
    assert result["summary"]["可买入"] == 6
    external_buys = [fund for fund in result["funds"] if fund["recommendation"]["tier"] == "可买入" and not fund["held"]]
    assert len(external_buys) == 5


def test_run_research_includes_held_fund_outside_universe() -> None:
    provider = _full_provider()
    provider._snapshots["000001"] = _snapshot(
        "000001", "华夏成长混合", performance={"1m": 0.5, "3m": 1.0, "6m": 2.0, "1y": 4.0},
        volatility=15.0, max_drawdown=-8.0, positive_ratio=52.0, trend="up", category="主动权益",
    )
    service = FundMarketResearchService(FakeRepo(), market_provider=provider)
    try:
        result = service.run_research(held_codes=["000001"])
    finally:
        service.close()
    fund = next(item for item in result["funds"] if item["code"] == "000001")
    assert fund["held"] is True
    assert fund["category"] == "自定义"
    assert fund["recommendation"]["tier"] in {"长期持有", "减仓", "可买入", "观望"}
