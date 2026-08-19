from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.services.stock_portfolio import StockPortfolioService, parse_stock_portfolio_ocr


class FakeRepo:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._quotes = pl.DataFrame(rows or [])

    def get_enriched_latest(self) -> tuple[pl.DataFrame, date | None]:
        return self._quotes, date(2026, 8, 19) if not self._quotes.is_empty() else None

    def get_name_map(self, symbols: list[str] | None = None) -> dict[str, str]:
        names = {
            "600519.SH": "贵州茅台",
            "000001.SZ": "平安银行",
        }
        if symbols is None:
            return names
        return {symbol: names[symbol] for symbol in symbols if symbol in names}

    def get_instruments_asset(self, asset_type: str) -> pl.DataFrame:
        assert asset_type == "stock"
        return pl.DataFrame({
            "code": ["600519", "000001"],
            "symbol": ["600519.SH", "000001.SZ"],
            "name": ["贵州茅台", "平安银行"],
        })


def _service(tmp_path: Path) -> StockPortfolioService:
    return StockPortfolioService(
        tmp_path,
        FakeRepo([
            {
                "symbol": "600519.SH",
                "raw_close": 12.0,
                "close": 6.0,
                "change_pct": 0.025,
            },
            {
                "symbol": "000001.SZ",
                "raw_close": 8.0,
                "close": 4.0,
                "change_pct": -0.01,
            },
        ]),
    )


def test_upsert_calculates_from_unadjusted_price_and_persists(tmp_path: Path) -> None:
    service = _service(tmp_path)

    portfolio = service.upsert_position(
        "600519.SH",
        {"name": "用户输入名称", "buy_price": 10, "quantity": 100},
    )

    position = portfolio["positions"][0]
    assert position["name"] == "贵州茅台"
    assert position["cost_amount"] == 1000.0
    assert position["current_price"] == 12.0
    assert position["market_value"] == 1200.0
    assert position["profit_amount"] == 200.0
    assert position["profit_pct"] == 0.2
    assert position["change_pct"] == 0.025
    assert position["price_date"] == "2026-08-19"
    assert portfolio["summary"] == {
        "currency": "CNY",
        "position_count": 1,
        "total_cost_amount": 1000.0,
        "total_market_value": 1200.0,
        "total_profit_amount": 200.0,
        "profit_pct": 0.2,
    }

    restored = _service(tmp_path).get_portfolio()
    assert restored["positions"][0]["buy_price"] == 10.0
    assert restored["positions"][0]["quantity"] == 100.0


def test_missing_raw_price_does_not_mix_adjusted_close(tmp_path: Path) -> None:
    service = StockPortfolioService(
        tmp_path,
        FakeRepo([{"symbol": "600519.SH", "close": 6.0, "change_pct": 0.01}]),
    )

    portfolio = service.upsert_position(
        "600519.SH",
        {"name": "贵州茅台", "buy_price": 10, "quantity": 100},
    )

    position = portfolio["positions"][0]
    assert position["current_price"] is None
    assert position["market_value"] is None
    assert position["profit_amount"] is None
    assert position["profit_pct"] is None
    assert portfolio["summary"]["total_market_value"] is None


def test_invalid_position_does_not_replace_existing_data(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.upsert_position(
        "600519.SH",
        {"name": "贵州茅台", "buy_price": 10, "quantity": 100},
    )

    with pytest.raises(ValueError, match="买入价格"):
        service.upsert_position(
            "000001.SZ",
            {"name": "平安银行", "buy_price": 0, "quantity": 100},
        )

    assert [row["symbol"] for row in service.get_portfolio()["positions"]] == ["600519.SH"]


def test_upsert_updates_existing_position_and_delete_handles_missing(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.upsert_position(
        "600519.SH",
        {"name": "贵州茅台", "buy_price": 10, "quantity": 100},
    )
    updated = service.upsert_position(
        "600519.SH",
        {"name": "贵州茅台", "buy_price": 11, "quantity": 200},
    )

    assert len(updated["positions"]) == 1
    assert updated["positions"][0]["cost_amount"] == 2200.0
    assert service.delete_position("600519.SH")["positions"] == []
    with pytest.raises(KeyError):
        service.delete_position("600519.SH")


def test_parse_stock_portfolio_ocr_derives_total_cost_from_unit_cost() -> None:
    result = parse_stock_portfolio_ocr(
        """
        贵州茅台 600519
        持仓数量 100
        成本价 1500.50
        平安银行 000001
        数量 200
        持仓成本 2100.00
        """,
        {"600519": "600519.SH", "000001": "000001.SZ"},
        {"600519.SH": "贵州茅台", "000001.SZ": "平安银行"},
    )

    assert result["candidates"] == [
        {
            "code": "600519",
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "quantity": 100.0,
            "cost_amount": 150050.0,
            "buy_price": 1500.5,
        },
        {
            "code": "000001",
            "symbol": "000001.SZ",
            "name": "平安银行",
            "quantity": 200.0,
            "cost_amount": 2100.0,
            "buy_price": 10.5,
        },
    ]


def test_parse_stock_portfolio_ocr_warns_for_missing_values_and_skips_unknown_code() -> None:
    result = parse_stock_portfolio_ocr(
        "贵州茅台 600519\n只有股票代码\n未知股票 123456",
        {"600519": "600519.SH"},
        {"600519.SH": "贵州茅台"},
    )

    assert result["candidates"][0]["quantity"] is None
    assert any("未完整识别" in warning for warning in result["warnings"])
    assert any("123456" in warning for warning in result["warnings"])
