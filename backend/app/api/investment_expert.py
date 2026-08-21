"""Investment expert paper-agent API."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/investment-expert", tags=["investment-expert"])


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
    confirm_replace: bool = False
    available_cash: float | None = Field(default=None, ge=0, allow_inf_nan=False)


@router.get("/status")
def status(request: Request) -> dict:
    return _service(request).status()


@router.post("/runtime/start")
def start_runtime(request: Request) -> dict:
    return _service(request).start()


@router.post("/runtime/stop")
def stop_runtime(request: Request) -> dict:
    return _service(request).stop()


@router.post("/runtime/tick")
def run_runtime_once(request: Request) -> dict:
    return _service(request).run_paper_cycle_once()


@router.get("/portfolio-sync/preview")
def portfolio_sync_preview(request: Request) -> dict:
    return _service(request).stock_portfolio_sync_preview()


@router.post("/portfolio-sync")
def sync_portfolio(request: Request, payload: PortfolioSyncRequest) -> dict:
    try:
        return _service(request).sync_stock_portfolio(
            confirm_replace=payload.confirm_replace,
            available_cash=payload.available_cash,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/dataset/bootstrap")
def bootstrap_dataset(request: Request, payload: DatasetBootstrapRequest) -> dict:
    return _service(request).submit_dataset_bootstrap(
        years=payload.years,
        candidate_limit=payload.candidate_limit,
        download_minutes=payload.download_minutes,
    )


@router.post("/evolution/run")
def run_evolution(request: Request) -> dict:
    return _service(request).submit_evolution()


@router.post("/training/run")
def run_training(request: Request) -> dict:
    return _service(request).submit_model_training()


@router.get("/sessions")
def sessions(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    return {"sessions": _service(request).store.list_sessions(limit=limit)}


@router.get("/events")
def events(
    request: Request,
    session_id: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict:
    return {
        "events": _service(request).store.list_execution_events(
            session_id=session_id, limit=limit
        )
    }


@router.get("/trades")
def trades(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    return {
        "trades": _service(request).store.list_trade_history(limit=limit)
    }


@router.get("/policies")
def policies(request: Request) -> dict:
    service = _service(request)
    champion = service.store.get_champion()
    return {
        "champion_id": champion.id if champion else None,
        "policies": [
            policy.model_dump(mode="json") for policy in service.store.list_policies()
        ],
    }


@router.get("/models")
def models(request: Request) -> dict:
    service = _service(request)
    active = service.store.get_active_model()
    return {
        "active_model_id": active.id if active else None,
        "models": [
            model.model_dump(mode="json") for model in service.store.list_models()
        ],
    }


@router.get("/experiments")
def experiments(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    return {"experiments": _service(request).store.list_experiments(limit=limit)}
