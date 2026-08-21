from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.fund_portfolio import router
from app.services.fund_portfolio import FundPortfolioService


class FakeQuoteProvider:
    name = "fake"

    def fetch_quote(self, code: str) -> dict:
        return {
            "name": f"基金{code}",
            "official_nav": 1.0,
            "official_nav_date": "2026-08-12",
            "estimated_nav": 1.01,
            "estimated_change_pct": 1.0,
            "quote_time": "2026-08-13 15:00",
            "quote_source": self.name,
        }

    def close(self) -> None:
        return None


class FakeOcrProvider:
    name = "fake-ocr"

    def available(self) -> bool:
        return True

    def extract_text(self, image_bytes: bytes) -> str:
        assert image_bytes == b"image"
        return """
        易方达蓝筹精选混合
        基金代码 005827
        持有金额 12345.67
        持仓成本 11000
        """


def _client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.state.fund_portfolio_service = FundPortfolioService(
        tmp_path,
        quote_provider=FakeQuoteProvider(),
    )
    app.state.fund_ocr_provider = FakeOcrProvider()
    app.include_router(router)
    return TestClient(app)


class FakeMarketResearchService:
    def run_research(self, codes=None):
        return {
            "scope": "fund_market",
            "as_of": "2026-08-12",
            "funds": [
                {"code": codes[0] if codes else "110020", "recommendation": {"tier": "观望"}}
            ],
            "data_gaps": [],
        }


def _market_client() -> TestClient:
    app = FastAPI()
    app.state.fund_market_research_service = FakeMarketResearchService()
    app.include_router(router)
    return TestClient(app)


def test_fund_market_research_run_works_without_positions() -> None:
    client = _market_client()
    response = client.post("/api/funds/research/run")
    assert response.status_code == 200
    assert response.json()["scope"] == "fund_market"

    with_codes = client.post("/api/funds/research/run", json={"codes": ["000171"]})
    assert with_codes.status_code == 200
    assert with_codes.json()["funds"][0]["code"] == "000171"


def test_fund_market_research_run_rejects_invalid_code(tmp_path: Path) -> None:
    client = _client(tmp_path)  # 未装配 market service
    response = client.post("/api/funds/research/run")
    assert response.status_code == 503


def test_csv_preview_does_not_write_until_confirmed(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = "基金代码,基金名称,持有金额\n005827,易方达蓝筹精选混合,12345.67\n".encode()

    preview = client.post(
        "/api/funds/import-preview",
        files={"file": ("funds.csv", payload, "text/csv")},
    )

    assert preview.status_code == 200
    candidate = preview.json()["candidates"][0]
    assert candidate["code"] == "005827"
    assert client.get("/api/funds/portfolio").json()["positions"] == []

    confirmed = client.post(
        "/api/funds/import-confirm",
        json={"source": "csv", "positions": [candidate]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["positions"][0]["code"] == "005827"


def test_image_preview_uses_configured_ocr_provider(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/funds/import-preview",
        files={"file": ("alipay.png", b"image", "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "alipay_screenshot"
    assert response.json()["candidates"][0]["holding_amount"] == 12345.67


def test_manual_upsert_refresh_and_delete(tmp_path: Path) -> None:
    client = _client(tmp_path)
    upserted = client.put(
        "/api/funds/positions/005827",
        json={"name": "易方达蓝筹精选混合", "shares": 1000, "cost_amount": 900},
    )
    assert upserted.status_code == 200

    refreshed = client.post("/api/funds/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh"]["updated"] == 1

    deleted = client.delete("/api/funds/positions/005827")
    assert deleted.status_code == 200
    assert deleted.json()["positions"] == []


def test_fund_lookup_returns_name_for_six_digit_code(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/funds/lookup/005827")

    assert response.status_code == 200
    assert response.json() == {"code": "005827", "name": "基金005827"}


def test_fund_lookup_rejects_invalid_code(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/funds/lookup/123")

    assert response.status_code == 404
    assert "6 位" in response.json()["detail"]
