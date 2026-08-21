from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.investment_expert import router


class _Store:
    def __init__(self) -> None:
        self.limits: list[int] = []

    def list_trade_history(self, *, limit: int) -> list[dict]:
        self.limits.append(limit)
        return [{"id": "evt_1", "decision_reason": "vwap_and_opening_range_confirmed"}]


def test_trade_history_api_forwards_validated_limit() -> None:
    store = _Store()
    app = FastAPI()
    app.state.investment_expert_service = SimpleNamespace(store=store)
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/api/investment-expert/trades?limit=25")

    assert response.status_code == 200
    assert response.json() == {
        "trades": [{
            "id": "evt_1",
            "decision_reason": "vwap_and_opening_range_confirmed",
        }]
    }
    assert store.limits == [25]
    assert client.get("/api/investment-expert/trades?limit=0").status_code == 422


def test_portfolio_sync_preview_and_confirmation_api() -> None:
    class Service:
        def __init__(self) -> None:
            self.calls = []

        @staticmethod
        def stock_portfolio_sync_preview() -> dict:
            return {
                "can_sync": True,
                "positions": [{"symbol": "600000.SH", "quantity": 1_000}],
            }

        def sync_stock_portfolio(self, **kwargs) -> dict:
            self.calls.append(kwargs)
            return {"status": "succeeded", "sync": {"position_count": 1}}

    service = Service()
    app = FastAPI()
    app.state.investment_expert_service = service
    app.include_router(router)
    client = TestClient(app)

    preview = client.get("/api/investment-expert/portfolio-sync/preview")
    response = client.post(
        "/api/investment-expert/portfolio-sync",
        json={"confirm_replace": True, "available_cash": 88_888.0},
    )

    assert preview.status_code == 200
    assert preview.json()["positions"][0]["symbol"] == "600000.SH"
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert service.calls == [{"confirm_replace": True, "available_cash": 88_888.0}]
    assert client.post(
        "/api/investment-expert/portfolio-sync",
        json={"confirm_replace": True, "available_cash": -1},
    ).status_code == 422


def test_portfolio_sync_api_maps_conflicts() -> None:
    class Service:
        @staticmethod
        def sync_stock_portfolio(**_kwargs) -> dict:
            raise RuntimeError("请先停止 AI 投资专家盯盘")

    app = FastAPI()
    app.state.investment_expert_service = Service()
    app.include_router(router)
    client = TestClient(app)

    response = client.post(
        "/api/investment-expert/portfolio-sync",
        json={"confirm_replace": True},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "请先停止 AI 投资专家盯盘"
