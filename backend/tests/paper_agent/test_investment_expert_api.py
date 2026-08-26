from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.investment_expert import router


class _Service:
    def __init__(self) -> None:
        self.limits: list[int] = []

    def list_trade_history(self, *, limit: int) -> list[dict]:
        self.limits.append(limit)
        return [
            {
                "id": "evt_1",
                "session_id": "session_1",
                "trade_date": "2026-08-21",
                "order_id": "order_1",
                "symbol": "600000.SH",
                "side": "buy",
                "occurred_at": "2026-08-21T01:31:00+00:00",
                "fill_status": "order_filled",
                "shares": 100,
                "price": 10.0,
                "fees": 5.0,
                "realized_pnl": None,
                "execution_reason": "entry",
                "decision_id": "decision_1",
                "decision_time": "2026-08-21T01:30:00+00:00",
                "decision_action": "buy",
                "decision_reason": "vwap_and_opening_range_confirmed",
                "decision_features": {"vwap_bias": 0.01},
            }
        ]


def test_trade_history_api_forwards_validated_limit() -> None:
    service = _Service()
    app = FastAPI()
    app.state.investment_expert_service = service
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/api/investment-expert/trades?limit=25")

    assert response.status_code == 200
    assert response.json()["trades"][0]["decision_reason"] == ("vwap_and_opening_range_confirmed")
    assert service.limits == [25]
    assert client.get("/api/investment-expert/trades?limit=0").status_code == 422


def test_api_routes_declare_response_models() -> None:
    """非流式接口应公开稳定的 OpenAPI 响应契约。"""

    missing = [
        route.path
        for route in router.routes
        if getattr(route, "response_model", None) in (None, dict)
    ]

    assert missing == []


def test_portfolio_sync_preview_and_confirmation_api() -> None:
    """持仓同步接口应校验请求并输出稳定响应契约。"""

    class Service:
        """记录持仓同步参数的 API 服务替身。"""

        def __init__(self) -> None:
            """初始化调用记录。"""
            self.calls = []

        @staticmethod
        def stock_portfolio_sync_preview() -> dict:
            """返回完整的持仓同步预检结构。"""
            return {
                "can_sync": True,
                "positions": [
                    {
                        "symbol": "600000.SH",
                        "name": "浦发银行",
                        "quantity": 1_000,
                        "entry_price": 10.0,
                        "current_price": 10.5,
                        "acquired_date": "2026-08-21",
                        "cost_amount": 10_000.0,
                        "market_value": 10_500.0,
                    }
                ],
                "errors": [],
                "warnings": [],
            }

        def sync_stock_portfolio(self, **kwargs) -> dict:
            """记录确认参数并返回完整的同步审计摘要。"""
            self.calls.append(kwargs)
            return {
                "status": "succeeded",
                "sync": {
                    "id": "portfolio_sync_1",
                    "source": "stock_portfolio",
                    "mode": "replace",
                    "created_at": "2026-08-21T10:00:00+00:00",
                    "position_count": 1,
                    "cash": 88_888.0,
                    "equity": 99_388.0,
                    "payload_hash": "hash",
                },
            }

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
    assert (
        client.post(
            "/api/investment-expert/portfolio-sync",
            json={"confirm_replace": True, "available_cash": -1},
        ).status_code
        == 422
    )


def test_portfolio_sync_api_maps_conflicts() -> None:
    """运行态冲突应稳定映射为 HTTP 409。"""

    class Service:
        """始终返回业务冲突的服务替身。"""

        @staticmethod
        def sync_stock_portfolio(**_kwargs) -> dict:
            """模拟同步期间的业务冲突。"""
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


def test_strategy_lab_task_routes_forward_to_service() -> None:
    """策略优化和生成接口必须把任务转发给服务层。"""

    class Service:
        """记录策略实验接口所需的最小服务替身。"""

        @staticmethod
        def submit_strategy_optimization() -> dict[str, str]:
            """模拟提交策略参数优化任务。"""
            return {"status": "started", "task": "strategy_optimization"}

        @staticmethod
        def submit_strategy_generation() -> dict[str, str]:
            """模拟提交 AI 策略生成任务。"""
            return {"status": "started", "task": "strategy_generation"}

    app = FastAPI()
    app.state.investment_expert_service = Service()
    app.include_router(router)
    client = TestClient(app)

    optimized = client.post("/api/investment-expert/strategy/optimize")
    generated = client.post("/api/investment-expert/strategy/generate")

    assert optimized.status_code == 200
    assert optimized.json() == {
        "status": "started",
        "task": "strategy_optimization",
        "reason": None,
    }
    assert generated.status_code == 200
    assert generated.json() == {"status": "started", "task": "strategy_generation", "reason": None}
