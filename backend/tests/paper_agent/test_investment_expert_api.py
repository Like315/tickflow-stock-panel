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
