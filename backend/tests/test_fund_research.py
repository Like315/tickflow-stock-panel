from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from app.services.fund_research import (
    EastmoneyFundResearchProvider,
    FundResearchService,
    calculate_nav_metrics,
)


class FakePortfolioService:
    def get_portfolio(self) -> dict:
        return {
            "source": "csv",
            "synced_at": "2026-08-13T10:00:00+08:00",
            "quotes_refreshed_at": "2026-08-13T15:00:00+08:00",
            "summary": {
                "currency": "CNY",
                "position_count": 3,
                "total_market_value": 100_000.0,
                "total_cost_amount": 90_000.0,
                "total_holding_profit": 10_000.0,
                "holding_profit_pct": 11.11,
                "total_day_profit": 100.0,
            },
            "positions": [
                {
                    "code": "005827",
                    "name": "易方达蓝筹精选混合",
                    "market_value": 60_000.0,
                    "cost_amount": 50_000.0,
                    "holding_profit": 10_000.0,
                    "holding_profit_pct": 20.0,
                    "day_profit": 80.0,
                    "shares": 10_000.0,
                    "official_nav": 1.55,
                    "official_nav_date": "2026-08-12",
                    "estimated_nav": None,
                    "estimated_change_pct": 0.24,
                    "quote_status": "official",
                },
                {
                    "code": "110011",
                    "name": "易方达中小盘混合",
                    "market_value": 30_000.0,
                    "cost_amount": 30_000.0,
                    "holding_profit": 0.0,
                    "holding_profit_pct": 0.0,
                    "day_profit": 10.0,
                    "shares": 8_000.0,
                    "official_nav": 2.1,
                    "official_nav_date": "2026-08-12",
                    "estimated_nav": None,
                    "estimated_change_pct": 0.05,
                    "quote_status": "official",
                },
                {
                    "code": "000001",
                    "name": "华夏成长混合",
                    "market_value": 10_000.0,
                    "cost_amount": 10_000.0,
                    "holding_profit": 0.0,
                    "holding_profit_pct": 0.0,
                    "day_profit": 10.0,
                    "shares": None,
                    "official_nav": None,
                    "official_nav_date": None,
                    "estimated_nav": None,
                    "estimated_change_pct": None,
                    "quote_status": None,
                },
            ],
        }


class FakeMarketProvider:
    def research_snapshot(self, code: str) -> dict:
        assert code in {"005827", "110011", "000001"}
        return {
            "code": code,
            "name": f"基金{code}",
            "fund_type": "混合型-偏股",
            "company": "示例基金公司",
            "managers": ["基金经理甲"],
            "nav_as_of": "2026-08-12",
            "performance_pct": {"1m": 2.0, "3m": 4.0, "6m": -3.0, "1y": 8.0},
            "annualized_volatility_pct": 18.0,
            "max_drawdown_1y_pct": -12.0,
            "positive_day_ratio_pct": 52.0,
            "sample_days": 250,
            "source": "public_fund_nav",
        }

    def close(self) -> None:
        return None


class FakeResponse:
    def __init__(self, *, document: dict | None = None, text: str = "") -> None:
        self._document = document
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        assert self._document is not None
        return self._document


class FakeEastmoneyClient:
    def get(self, url: str, **_kwargs) -> FakeResponse:
        if "FundSearchAPI" in url:
            return FakeResponse(
                document={
                    "Datas": [
                        {
                            "CODE": "005827",
                            "NAME": "易方达蓝筹精选混合",
                            "CATEGORYDESC": "混合型",
                            "FundBaseInfo": {"FTYPE": "混合型-偏股", "JJGS": "易方达基金"},
                        }
                    ],
                }
            )
        if url.endswith("/pingzhongdata/005827.js"):
            return FakeResponse(
                text=(
                    'var unrelated = "ignore";\n'
                    'var Data_assetAllocation = {"series":['
                    '{"name":"股票占净比","data":[80.0]},'
                    '{"name":"债券占净比","data":[5.0]},'
                    '{"name":"现金占净比","data":[15.0]}],'
                    '"categories":["2026-06-30"]};\n'
                    "var Data_netWorthTrend = ["
                    '{"x":1753977600000,"y":1.0},'
                    '{"x":1756656000000,"y":1.1},'
                    '{"x":1785513600000,"y":1.2}'
                    "];\nvar after = true;"
                )
            )
        assert url.endswith("/FundArchivesDatas.aspx")
        html = (
            "<h4>截止至: 2026-06-30</h4><table><tbody><tr>"
            "<td>1</td><td>600000</td><td>浦发银行</td><td></td><td></td><td></td>"
            "<td>8.50%</td><td>100.00</td><td>2,000.00</td></tr></tbody></table>"
        )
        return FakeResponse(
            text=f"var apidata={{ content:{json.dumps(html, ensure_ascii=False)},arryear:[]}};"
        )

    def close(self) -> None:
        return None


def test_calculate_nav_metrics_has_explicit_percent_units() -> None:
    start = date(2025, 8, 1)
    rows = [
        {"date": (start + timedelta(days=index)).isoformat(), "nav": 1 + index / 1000}
        for index in range(370)
    ]

    result = calculate_nav_metrics(rows)

    assert result["sample_days"] == 370
    assert result["performance_pct"]["1y"] == pytest.approx(36.35)
    assert result["max_drawdown_1y_pct"] == pytest.approx(0.0)
    assert result["annualized_volatility_pct"] >= 0


def test_eastmoney_provider_parses_complete_nav_trend_without_executing_javascript() -> None:
    provider = EastmoneyFundResearchProvider(client=FakeEastmoneyClient())

    result = provider.research_snapshot("005827")

    assert result["name"] == "易方达蓝筹精选混合"
    assert result["sample_days"] == 3
    assert result["nav_as_of"] == "2026-08-01"
    assert result["performance_pct"]["1y"] == pytest.approx(20.0)
    assert result["asset_allocation"]["stock_pct"] == pytest.approx(80.0)
    assert result["top_holdings_as_of"] == "2026-06-30"
    assert result["top_holdings"][0]["security_name"] == "浦发银行"
    assert result["top_holdings"][0]["market_segment"] == "沪市证券"


def test_portfolio_context_calculates_concentration_and_profit_contribution() -> None:
    service = FundResearchService(
        FakePortfolioService(),
        market_provider=FakeMarketProvider(),
        max_market_positions=2,
    )

    context = service.build_context()

    assert context["scope"] == "portfolio"
    assert context["currency"] == "CNY"
    assert context["analytics"]["top1_weight_pct"] == pytest.approx(60.0)
    assert context["analytics"]["top3_weight_pct"] == pytest.approx(100.0)
    assert context["analytics"]["hhi"] == pytest.approx(0.46)
    assert context["positions"][0]["holding_profit_contribution_pct"] == pytest.approx(100.0)
    assert len(context["market_research"]) == 2
    assert any("仅补充了市值最大的 2 只基金" in item for item in context["data_gaps"])
    assert any("缺少正式净值日期" in item for item in context["data_gaps"])
    assessments = {item["code"]: item for item in context["operation_assessments"]}
    assert assessments["005827"]["tier"] == "降低风险暴露"
    assert any("35.00%" in item for item in assessments["005827"]["reasons"])
    assert assessments["110011"]["tier"] == "继续持有观察"


def test_single_fund_context_keeps_portfolio_role() -> None:
    service = FundResearchService(
        FakePortfolioService(),
        market_provider=FakeMarketProvider(),
    )

    context = service.build_context("110011")

    assert context["scope"] == "fund"
    assert context["fund_code"] == "110011"
    assert context["position"]["weight_pct"] == pytest.approx(30.0)
    assert context["market_research"][0]["fund_type"] == "混合型-偏股"
    assert context["operation_assessments"][0]["tier"] == "继续持有观察"


def test_operation_policy_marks_loss_and_negative_medium_term_as_sell_review() -> None:
    class SellMarketProvider(FakeMarketProvider):
        def research_snapshot(self, code: str) -> dict:
            result = super().research_snapshot(code)
            if code == "000001":
                result["performance_pct"] = {"1m": -2.0, "3m": -4.0, "6m": -8.0, "1y": 3.0}
            return result

    service = FundResearchService(
        FakePortfolioService(),
        market_provider=SellMarketProvider(),
    )

    context = service.build_context()

    assessment = next(item for item in context["operation_assessments"] if item["code"] == "000001")
    assert assessment["tier"] == "继续持有观察"

    portfolio = FakePortfolioService().get_portfolio()
    portfolio["positions"][2]["holding_profit"] = -100
    portfolio["positions"][2]["holding_profit_pct"] = -1.0

    class LosingPortfolio:
        def get_portfolio(self) -> dict:
            return portfolio

    losing_service = FundResearchService(
        LosingPortfolio(),
        market_provider=SellMarketProvider(),
    )
    losing_context = losing_service.build_context()
    losing = next(
        item for item in losing_context["operation_assessments"] if item["code"] == "000001"
    )
    assert losing["tier"] == "进入卖出评估"


def test_unknown_or_empty_portfolio_fails_closed() -> None:
    service = FundResearchService(
        FakePortfolioService(),
        market_provider=FakeMarketProvider(),
    )
    with pytest.raises(ValueError, match="不在当前基金账本"):
        service.build_context("999999")

    class EmptyPortfolio:
        def get_portfolio(self) -> dict:
            return {"summary": {"position_count": 0}, "positions": []}

    empty = FundResearchService(EmptyPortfolio(), market_provider=FakeMarketProvider())
    with pytest.raises(ValueError, match="基金账本为空"):
        empty.build_context()
