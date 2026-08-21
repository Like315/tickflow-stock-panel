from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.stock_portfolio import router
from app.services.stock_portfolio import StockPortfolioService


class FakeRepo:
    def get_enriched_latest(self) -> tuple[pl.DataFrame, date]:
        return (
            pl.DataFrame(
                {
                    "symbol": ["600519.SH"],
                    "raw_close": [1520.0],
                    "change_pct": [0.01],
                }
            ),
            date(2026, 8, 19),
        )

    def get_name_map(self, symbols: list[str] | None = None) -> dict[str, str]:
        return {"600519.SH": "贵州茅台"}

    def get_instruments_asset(self, asset_type: str) -> pl.DataFrame:
        assert asset_type == "stock"
        return pl.DataFrame(
            {
                "code": ["600519"],
                "symbol": ["600519.SH"],
                "name": ["贵州茅台"],
            }
        )


class FakeOcrProvider:
    name = "fake-ocr"

    def available(self) -> bool:
        return True

    def extract_text(self, image_bytes: bytes) -> str:
        assert image_bytes == b"image"
        return "贵州茅台 600519\n持仓数量 100\n成本价 1500.50"


def _client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.state.stock_portfolio_service = StockPortfolioService(tmp_path, FakeRepo())
    app.state.stock_ocr_provider = FakeOcrProvider()
    app.include_router(router)
    return TestClient(app)


def test_empty_portfolio_and_manual_position_lifecycle(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/stock-portfolio").json()["positions"] == []

    created = client.put(
        "/api/stock-portfolio/positions/600519.SH",
        json={"name": "贵州茅台", "buy_price": 1500, "quantity": 10},
    )
    assert created.status_code == 200
    assert created.json()["positions"][0]["profit_amount"] == 200.0

    updated = client.put(
        "/api/stock-portfolio/positions/600519.SH",
        json={"name": "贵州茅台", "buy_price": 1510, "quantity": 20},
    )
    assert updated.status_code == 200
    assert updated.json()["positions"][0]["cost_amount"] == 30200.0

    deleted = client.delete("/api/stock-portfolio/positions/600519.SH")
    assert deleted.status_code == 200
    assert deleted.json()["positions"] == []
    assert client.delete("/api/stock-portfolio/positions/600519.SH").status_code == 404


def test_api_rejects_invalid_price_quantity_and_symbol(tmp_path: Path) -> None:
    client = _client(tmp_path)

    assert (
        client.put(
            "/api/stock-portfolio/positions/600519.SH",
            json={"name": "贵州茅台", "buy_price": 0, "quantity": 10},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/stock-portfolio/positions/600519.SH",
            json={"name": "贵州茅台", "buy_price": 10, "quantity": 0},
        ).status_code
        == 422
    )
    invalid_symbol = client.put(
        "/api/stock-portfolio/positions/not-a-stock",
        json={"name": "未知", "buy_price": 10, "quantity": 10},
    )
    assert invalid_symbol.status_code == 400


def test_image_import_returns_editable_preview_without_writing(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/stock-portfolio/import-preview",
        files={"file": ("holding.png", b"image", "image/png")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "fake-ocr"
    assert body["candidates"][0] == {
        "code": "600519",
        "symbol": "600519.SH",
        "name": "贵州茅台",
        "quantity": 100.0,
        "cost_amount": 150050.0,
        "buy_price": 1500.5,
    }
    assert client.get("/api/stock-portfolio").json()["positions"] == []


def test_image_import_rejects_non_image(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/api/stock-portfolio/import-preview",
        files={"file": ("holding.txt", b"image", "text/plain")},
    )
    assert response.status_code == 400
