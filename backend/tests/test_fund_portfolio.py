from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.services.fund_portfolio import (
    EastmoneyFundQuoteProvider,
    FundPortfolioService,
    parse_csv_snapshot,
    parse_localized_number,
    parse_ocr_snapshot,
)


class FakeQuoteProvider:
    name = "fake"

    def fetch_quote(self, code: str) -> dict:
        assert code == "005827"
        return {
            "name": "易方达蓝筹精选混合",
            "official_nav": 2.1000,
            "official_nav_date": "2026-08-12",
            "estimated_nav": 2.1210,
            "estimated_change_pct": 1.0,
            "quote_time": "2026-08-13 15:00",
            "quote_source": self.name,
        }

    def close(self) -> None:
        return None


def test_default_quote_provider_supports_socks_proxy(monkeypatch) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:9")

    provider = EastmoneyFundQuoteProvider()
    provider.close()


def test_parse_localized_number_supports_currency_percent_and_wan() -> None:
    assert parse_localized_number("￥12,345.67") == Decimal("12345.67")
    assert parse_localized_number("1.25万") == Decimal("12500")
    assert parse_localized_number("+8.6%") == Decimal("8.6")
    assert parse_localized_number("--") is None


def test_parse_csv_snapshot_maps_alipay_style_headers() -> None:
    payload = (
        "基金代码,基金名称,持有金额,持有份额,持仓成本,持有收益,昨日收益\n"
        "005827,易方达蓝筹精选混合,12345.67,5800.12,11000,1345.67,21.30\n"
    ).encode("utf-8-sig")

    result = parse_csv_snapshot(payload)

    assert result["warnings"] == []
    assert result["candidates"] == [
        {
            "code": "005827",
            "name": "易方达蓝筹精选混合",
            "holding_amount": 12345.67,
            "shares": 5800.12,
            "cost_amount": 11000.0,
            "holding_profit": 1345.67,
            "holding_profit_pct": None,
            "day_profit": 21.3,
        }
    ]


def test_parse_ocr_snapshot_extracts_labeled_fund_block() -> None:
    text = """
    支付宝基金
    易方达蓝筹精选混合
    基金代码 005827
    持有金额 ￥12,345.67
    持有份额 5,800.12
    持仓成本 11,000.00
    持有收益 +1,345.67
    持有收益率 +12.23%
    昨日收益 +21.30
    """

    result = parse_ocr_snapshot(text)

    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["code"] == "005827"
    assert candidate["name"] == "易方达蓝筹精选混合"
    assert candidate["holding_amount"] == pytest.approx(12345.67)
    assert candidate["holding_profit_pct"] == pytest.approx(12.23)


def test_replace_snapshot_calculates_summary_and_persists(tmp_path: Path) -> None:
    service = FundPortfolioService(tmp_path, quote_provider=FakeQuoteProvider())
    service.replace_positions(
        [
            {
                "code": "005827",
                "name": "易方达蓝筹精选混合",
                "holding_amount": 12_000,
                "cost_amount": 10_000,
                "day_profit": 20,
            }
        ],
        source="alipay_screenshot",
    )

    portfolio = service.get_portfolio()

    assert portfolio["summary"]["currency"] == "CNY"
    assert portfolio["summary"]["total_market_value"] == pytest.approx(12_000)
    assert portfolio["summary"]["total_holding_profit"] == pytest.approx(2_000)
    assert portfolio["summary"]["holding_profit_pct"] == pytest.approx(20)
    assert portfolio["positions"][0]["day_profit"] == pytest.approx(20)

    reloaded = FundPortfolioService(tmp_path, quote_provider=FakeQuoteProvider())
    assert reloaded.get_portfolio()["positions"][0]["code"] == "005827"


def test_refresh_quote_updates_market_value_and_estimated_day_profit(tmp_path: Path) -> None:
    service = FundPortfolioService(tmp_path, quote_provider=FakeQuoteProvider())
    service.replace_positions(
        [
            {
                "code": "005827",
                "name": "",
                "shares": 5_000,
                "cost_amount": 10_000,
            }
        ],
        source="manual",
    )

    result = service.refresh_quotes()

    assert result["refresh"]["updated"] == 1
    position = result["portfolio"]["positions"][0]
    assert position["market_value"] == pytest.approx(10_605)
    assert position["holding_profit"] == pytest.approx(605)
    assert position["day_profit"] == pytest.approx(105)
    assert position["day_profit_estimated"] is True
    assert position["quote_status"] == "estimate"


def test_lookup_fund_returns_provider_name_without_changing_portfolio(tmp_path: Path) -> None:
    service = FundPortfolioService(tmp_path, quote_provider=FakeQuoteProvider())

    result = service.lookup_fund("005827")

    assert result == {"code": "005827", "name": "易方达蓝筹精选混合"}
    assert service.get_portfolio()["positions"] == []


class FakeOfficialQuoteProvider:
    name = "fake-official"

    def fetch_quote(self, code: str) -> dict:
        return {
            "name": "基金",
            "official_nav": 1.2,
            "official_nav_date": "2026-08-12",
            "estimated_nav": None,
            "estimated_change_pct": 0.5,
            "quote_time": None,
            "quote_source": self.name,
        }

    def close(self) -> None:
        return None


def test_refresh_official_nav_is_not_labeled_as_intraday_estimate(tmp_path: Path) -> None:
    service = FundPortfolioService(tmp_path, quote_provider=FakeOfficialQuoteProvider())
    service.replace_positions(
        [{"code": "005827", "shares": 1_000, "cost_amount": 1_000}],
        source="manual",
    )

    position = service.refresh_quotes()["portfolio"]["positions"][0]

    assert position["market_value"] == pytest.approx(1_200)
    assert position["quote_status"] == "official"
    assert position["day_profit_estimated"] is False


def test_invalid_snapshot_does_not_replace_existing_positions(tmp_path: Path) -> None:
    service = FundPortfolioService(tmp_path, quote_provider=FakeQuoteProvider())
    service.replace_positions(
        [{"code": "005827", "holding_amount": 100}],
        source="manual",
    )

    with pytest.raises(ValueError, match="至少填写"):
        service.replace_positions([{"code": "000001"}], source="manual")

    assert service.get_portfolio()["positions"][0]["code"] == "005827"


def test_refresh_preserves_manual_edit_made_while_quote_is_loading(tmp_path: Path) -> None:
    service: FundPortfolioService

    class EditingProvider(FakeQuoteProvider):
        def fetch_quote(self, code: str) -> dict:
            service.upsert_position(code, {"shares": 6_000, "cost_amount": 10_000})
            return super().fetch_quote(code)

    service = FundPortfolioService(tmp_path, quote_provider=EditingProvider())
    service.replace_positions(
        [{"code": "005827", "shares": 5_000, "cost_amount": 10_000}],
        source="manual",
    )

    position = service.refresh_quotes()["portfolio"]["positions"][0]

    assert position["shares"] == pytest.approx(6_000)
    assert position["market_value"] == pytest.approx(12_726)


def test_refresh_failure_keeps_existing_financial_values(tmp_path: Path) -> None:
    class FailingProvider(FakeQuoteProvider):
        def fetch_quote(self, code: str) -> dict:
            raise RuntimeError("provider unavailable")

    service = FundPortfolioService(tmp_path, quote_provider=FailingProvider())
    service.replace_positions(
        [{"code": "005827", "holding_amount": 1_200, "cost_amount": 1_000}],
        source="manual",
    )

    result = service.refresh_quotes()

    assert result["refresh"]["updated"] == 0
    assert result["refresh"]["failed"] == 1
    assert result["portfolio"]["positions"][0]["market_value"] == pytest.approx(1_200)
    assert result["portfolio"]["positions"][0]["holding_profit"] == pytest.approx(200)
