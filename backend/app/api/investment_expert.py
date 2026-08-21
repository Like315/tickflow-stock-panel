"""Investment expert paper-agent API."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.paper_agent.models import ExecutionEvent, ExpertPolicy, TrainedDecisionModel

router = APIRouter(prefix="/api/investment-expert", tags=["investment-expert"])
PortfolioSyncBlockedReason = Literal[
    "runtime_running",
    "background_task_running",
    "source_portfolio_empty",
    "invalid_source_positions",
    "stock_portfolio_service_unavailable",
]


def _service(request: Request):
    service = request.app.state.investment_expert_service
    capset = getattr(request.app.state, "capabilities", None)
    if capset is not None:
        service.update_capabilities(capset)
    return service


class DatasetBootstrapRequest(BaseModel):
    years: int = Field(default=3, ge=1, le=5)
    candidate_limit: int = Field(default=50, ge=5, le=200)
    download_minutes: bool = True


class PortfolioSyncRequest(BaseModel):
    """股票持仓覆盖同步请求。"""

    confirm_replace: bool = Field(
        default=False,
        description="是否明确确认覆盖 AI 当前持仓。",
    )
    available_cash: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        description="同步后保留的可用现金, 未提供时按零处理。",
    )


class PortfolioSyncPositionResponse(BaseModel):
    """可同步的单只股票持仓。"""

    symbol: str
    name: str
    quantity: int
    entry_price: float
    current_price: float
    acquired_date: str
    cost_amount: float
    market_value: float


class PortfolioSyncPreviewResponse(BaseModel):
    """股票持仓同步预检响应。"""

    can_sync: bool
    blocked_reason: PortfolioSyncBlockedReason | None = None
    source: Literal["stock_portfolio"] | None = None
    source_updated_at: str | None = None
    positions: list[PortfolioSyncPositionResponse] = Field(default_factory=list)
    position_count: int | None = None
    source_total_cost_amount: float | None = None
    source_total_market_value: float | None = None
    replace_position_count: int | None = None
    current_available_cash: float | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PortfolioSyncSummaryResponse(BaseModel):
    """已落库的股票持仓同步摘要。"""

    id: str
    source: Literal["stock_portfolio"]
    mode: Literal["replace"]
    created_at: str
    position_count: int
    cash: float
    equity: float
    payload_hash: str


class PortfolioSyncResponse(BaseModel):
    """股票持仓同步成功响应。"""

    status: Literal["succeeded"]
    sync: PortfolioSyncSummaryResponse


class RuntimeActionResponse(BaseModel):
    """运行时启停响应。"""

    status: Literal["blocked", "reused", "started", "stopped"]
    running: bool
    reason: str | None = None


class PaperCycleResponse(BaseModel):
    """单次盯盘循环响应；阶段明细由运行阶段扩展。"""

    model_config = ConfigDict(extra="allow")

    status: str
    reason: str | None = None


class TaskSubmissionResponse(BaseModel):
    """后台任务提交响应。"""

    status: Literal["started", "reused", "deferred"]
    task: Literal["dataset_bootstrap", "evolution", "model_training"] | None = None
    reason: str | None = None


class TradingSessionResponse(BaseModel):
    """模拟交易会话。"""

    id: str
    trade_date: date
    policy_id: str
    mode: Literal["paper", "replay", "shadow"]
    status: str
    started_at: str
    finished_at: str | None = None
    candidates: list[str] = Field(default_factory=list)
    summary: dict[str, JsonValue] = Field(default_factory=dict)


class SessionsResponse(BaseModel):
    sessions: list[TradingSessionResponse]


class ExecutionEventsResponse(BaseModel):
    events: list[ExecutionEvent]


class TradeHistoryItemResponse(BaseModel):
    """成交记录及其对应的决策快照。"""

    id: str | None = None
    session_id: str
    trade_date: date
    order_id: str | None = None
    symbol: str | None = None
    side: Literal["buy", "sell"] | None = None
    occurred_at: str
    fill_status: Literal["order_filled", "order_partially_filled"]
    shares: int = Field(ge=0)
    price: float | None = None
    fees: float = 0.0
    realized_pnl: float | None = None
    execution_reason: str | None = None
    decision_id: str | None = None
    decision_time: str | None = None
    decision_action: str | None = None
    decision_reason: str | None = None
    decision_features: dict[str, JsonValue] | None = None


class TradeHistoryResponse(BaseModel):
    trades: list[TradeHistoryItemResponse]


class PolicyCatalogResponse(BaseModel):
    champion_id: str | None = None
    policies: list[ExpertPolicy]


class ModelCatalogResponse(BaseModel):
    active_model_id: str | None = None
    models: list[TrainedDecisionModel]


class EvolutionExperimentResponse(BaseModel):
    id: str
    champion_policy_id: str
    candidate_policy_id: str
    mutation_field: str
    status: str
    champion_metrics: dict[str, JsonValue]
    candidate_metrics: dict[str, JsonValue]
    reason: str
    created_at: str
    finished_at: str | None = None


class ExperimentsResponse(BaseModel):
    experiments: list[EvolutionExperimentResponse]


class DatasetRunResponse(BaseModel):
    id: str
    status: str
    start_date: date
    end_date: date
    manifest: dict[str, JsonValue]
    error: str | None = None
    started_at: str
    finished_at: str | None = None


class PositionStatusResponse(BaseModel):
    lot_id: str
    symbol: str
    acquired_date: date
    shares: int = Field(gt=0)
    remaining_shares: int = Field(gt=0)
    entry_price: float = Field(gt=0)
    entry_cost: float = Field(ge=0)
    cost_basis: float = Field(ge=0)
    market_price: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pnl_pct: float | None = None


class PerformanceResponse(BaseModel):
    filled_order_count: int = Field(ge=0)
    buy_order_count: int = Field(ge=0)
    sell_order_count: int = Field(ge=0)
    closed_trade_count: int = Field(ge=0)
    winning_trade_count: int = Field(ge=0)
    losing_trade_count: int = Field(ge=0)
    breakeven_trade_count: int = Field(ge=0)
    realized_pnl: float
    win_rate: float | None = None
    average_win_pnl: float | None = None
    average_loss_pnl: float | None = None
    profit_loss_ratio: float | None = None
    latest_fill_at: str | None = None
    position_count: int = Field(ge=0)
    position_lot_count: int = Field(ge=0)
    unpriced_position_count: int = Field(ge=0)
    unrealized_pnl: float | None = None
    total_pnl: float
    total_return: float
    valuation_as_of: str | None = None


class PortfolioSyncStatusResponse(BaseModel):
    id: str
    source: Literal["stock_portfolio"]
    mode: Literal["replace"]
    created_at: str
    source_updated_at: str | None = None
    position_count: int = Field(ge=0)
    cash: float
    equity: float


class MinuteCoverageResponse(BaseModel):
    start: date | None = None
    end: date | None = None


class InvestmentExpertStatusResponse(BaseModel):
    """投资专家运行状态和账户表现。"""

    champion: ExpertPolicy | None = None
    latest_session: TradingSessionResponse | None = None
    dataset: DatasetRunResponse | None = None
    enabled: bool
    active_model: TrainedDecisionModel | None = None
    latest_model: TrainedDecisionModel | None = None
    model_runtime_status: Literal["active", "baseline", "disabled", "not_activated"]
    running: bool
    active_task: str | None = None
    last_error: str | None = None
    session_id: str | None = None
    candidate_count: int = Field(ge=0)
    market_symbol_count: int = Field(ge=0)
    cash: float
    equity: float
    positions: list[PositionStatusResponse]
    performance: PerformanceResponse
    portfolio_baseline_equity: float
    portfolio_sync: PortfolioSyncStatusResponse | None = None
    pending_order_count: int = Field(ge=0)
    entries_enabled: bool
    risk_trip_reason: str | None = None
    overnight_us_market: dict[str, JsonValue] | None = None
    news_sentiment: dict[str, JsonValue] | None = None
    session_prepare_error: str | None = None
    minute_capable: bool
    live_minute_source: str
    historical_minute_source: str
    historical_minute_error: str | None = None
    historical_minute_capable: bool
    historical_minute_max_years: int | None = None
    historical_minute_remote_three_year_capable: bool
    historical_minute_archive_fallback_capable: bool
    historical_minute_archive_fallback_source: str | None = None
    historical_minute_local_coverage: dict[str, MinuteCoverageResponse]
    historical_minute_three_year_capable: bool
    historical_minute_three_year_error: str | None = None
    live_minute_mode: Literal["intraday_batch", "historical_batch_fallback"]


@router.get("/status", response_model=InvestmentExpertStatusResponse)
def status(request: Request) -> dict[str, object]:
    return _service(request).status()


@router.post("/runtime/start", response_model=RuntimeActionResponse)
def start_runtime(request: Request) -> RuntimeActionResponse:
    return RuntimeActionResponse.model_validate(_service(request).start())


@router.post("/runtime/stop", response_model=RuntimeActionResponse)
def stop_runtime(request: Request) -> RuntimeActionResponse:
    return RuntimeActionResponse.model_validate(_service(request).stop())


@router.post("/runtime/tick", response_model=PaperCycleResponse)
def run_runtime_once(request: Request) -> PaperCycleResponse:
    return PaperCycleResponse.model_validate(_service(request).run_paper_cycle_once())


@router.get("/portfolio-sync/preview", response_model=PortfolioSyncPreviewResponse)
def portfolio_sync_preview(request: Request) -> PortfolioSyncPreviewResponse:
    """预检股票持仓并返回可覆盖同步的规范化数据。"""
    return PortfolioSyncPreviewResponse.model_validate(
        _service(request).stock_portfolio_sync_preview()
    )


@router.post("/portfolio-sync", response_model=PortfolioSyncResponse)
def sync_portfolio(
    request: Request,
    payload: PortfolioSyncRequest,
) -> PortfolioSyncResponse:
    """明确确认后覆盖 AI 当前持仓并返回审计摘要。"""
    try:
        return PortfolioSyncResponse.model_validate(
            _service(request).sync_stock_portfolio(
                confirm_replace=payload.confirm_replace,
                available_cash=payload.available_cash,
            )
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/dataset/bootstrap", response_model=TaskSubmissionResponse)
def bootstrap_dataset(
    request: Request,
    payload: DatasetBootstrapRequest,
) -> TaskSubmissionResponse:
    return TaskSubmissionResponse.model_validate(
        _service(request).submit_dataset_bootstrap(
            years=payload.years,
            candidate_limit=payload.candidate_limit,
            download_minutes=payload.download_minutes,
        )
    )


@router.post("/evolution/run", response_model=TaskSubmissionResponse)
def run_evolution(request: Request) -> TaskSubmissionResponse:
    return TaskSubmissionResponse.model_validate(_service(request).submit_evolution())


@router.post("/training/run", response_model=TaskSubmissionResponse)
def run_training(request: Request) -> TaskSubmissionResponse:
    return TaskSubmissionResponse.model_validate(_service(request).submit_model_training())


@router.get("/sessions", response_model=SessionsResponse)
def sessions(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
) -> SessionsResponse:
    return SessionsResponse(sessions=_service(request).list_sessions(limit=limit))


@router.get("/events", response_model=ExecutionEventsResponse)
def events(
    request: Request,
    session_id: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
) -> ExecutionEventsResponse:
    return ExecutionEventsResponse(
        events=_service(request).list_execution_events(
            session_id=session_id,
            limit=limit,
        )
    )


@router.get("/trades", response_model=TradeHistoryResponse)
def trades(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> TradeHistoryResponse:
    return TradeHistoryResponse(trades=_service(request).list_trade_history(limit=limit))


@router.get("/policies", response_model=PolicyCatalogResponse)
def policies(request: Request) -> PolicyCatalogResponse:
    return PolicyCatalogResponse.model_validate(_service(request).policy_catalog())


@router.get("/models", response_model=ModelCatalogResponse)
def models(request: Request) -> ModelCatalogResponse:
    return ModelCatalogResponse.model_validate(_service(request).model_catalog())


@router.get("/experiments", response_model=ExperimentsResponse)
def experiments(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
) -> ExperimentsResponse:
    return ExperimentsResponse(experiments=_service(request).list_experiments(limit=limit))
