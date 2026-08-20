"""Backtest the C2, 14:30 confirmation, leader sleeve, and 30/20 blend."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from app.backtest.engine import BacktestEngine, MatcherConfig
from app.backtest.matrix import (
    MatrixPipelineConfig,
    MatrixStrategyPipeline,
    apply_time_masks,
    build_market_matrix_from_signals,
    make_signal_matrix,
    slice_market_data_matrix,
    slice_signal_matrix,
)
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.strategy.builtin.volume_dry_breakout import META as VOLUME_META
from app.strategy.engine import StrategyEngine
from app.strategy.research.capital_utilization_versions import (
    INTRADAY_CANDIDATE_STRATEGY,
    VERSIONS,
    CapitalUtilizationVersion,
    confirm_intraday_entries,
    summarize_activity,
)
from app.tickflow.repository import DataStore, KlineRepository

INITIAL_CAPITAL = 1_000_000.0
COMMISSION_PCT = 0.0002
STAMP_TAX_PCT = 0.0005
SLIPPAGE_BPS = 5.0


def _service(repo: KlineRepository) -> StrategyBacktestService:
    return StrategyBacktestService(
        engine=BacktestEngine(repo),
        strategy_engine=StrategyEngine(
            strategy_dirs=[
                Path(__file__).resolve().parent.parent / "app" / "strategy" / "builtin"
            ]
        ),
    )


def _config(
    version: CapitalUtilizationVersion,
    start: date,
    end: date,
    *,
    initial_capital: float = INITIAL_CAPITAL,
    max_exposure_pct: float | None = None,
    max_positions: int | None = None,
) -> StrategyBacktestConfig:
    return StrategyBacktestConfig(
        strategy_id=version.strategy_id,
        symbols=None,
        start=start,
        end=end,
        params=dict(version.params),
        overrides=dict(version.overrides),
        mode="position",
        matching="open_t+1",
        entry_fill="open_t+1",
        exit_fill="open_t+1",
        max_positions=version.max_positions if max_positions is None else max_positions,
        max_exposure_pct=(
            version.max_exposure_pct
            if max_exposure_pct is None
            else max_exposure_pct
        ),
        initial_capital=initial_capital,
        commission_pct=COMMISSION_PCT,
        stamp_tax_pct=STAMP_TAX_PCT,
        slippage_bps=SLIPPAGE_BPS,
    )


def _run_daily(
    service: StrategyBacktestService,
    config: StrategyBacktestConfig,
):
    prepared = service.prepare_matrix_optimization([config])
    try:
        return service.run(config, prepared=prepared)
    finally:
        prepared.compute_cache.close()


def _activity_row(
    version: CapitalUtilizationVersion,
    result,
    *,
    exposure_cap: float,
) -> dict[str, Any]:
    stats = result.stats or {}
    activity = summarize_activity(
        result.trades,
        result.equity_curve,
        initial_capital=float(stats.get("initial_capital") or INITIAL_CAPITAL),
        exposure_cap=exposure_cap,
    )
    return {
        "version": version.id,
        "label": version.label,
        "timing": version.timing,
        "status": "ok" if not result.error else "error",
        "error": result.error,
        "stats": {
            key: stats.get(key)
            for key in (
                "total_return",
                "annual_return",
                "max_drawdown",
                "sharpe",
                "sortino",
                "win_rate",
                "profit_factor",
                "n_trades",
                "initial_capital",
                "final_equity",
                "avg_exposure",
                "max_exposure",
            )
        },
        "activity": activity,
        "execution": stats.get("execution"),
    }


def _load_candidate_minutes(
    repo: KlineRepository,
    market,
    signals,
) -> pl.DataFrame:
    points = np.argwhere(signals.entry != 0)
    if not points.size:
        return pl.DataFrame()
    symbols = sorted({market.symbols[int(asset)] for _, asset in points})
    dates = sorted({
        date.fromisoformat(market.timestamp_labels[int(time_id)][:10])
        for time_id, _ in points
    })
    try:
        minute = repo.get_minute_by_dates(symbols, dates, asset_type="stock")
    except Exception:
        return pl.DataFrame()
    return minute if isinstance(minute, pl.DataFrame) else pl.DataFrame()


def _run_intraday(
    service: StrategyBacktestService,
    repo: KlineRepository,
    version: CapitalUtilizationVersion,
    config: StrategyBacktestConfig,
) -> tuple[Any | None, dict[str, Any]]:
    prepared = service.prepare_matrix_optimization([config])
    try:
        market = prepared.market_data
        pipeline_config = MatrixPipelineConfig(
            basic_filter=dict(VOLUME_META.get("basic_filter") or {}),
            scoring=dict(VOLUME_META.get("scoring") or {}),
            order_by=VOLUME_META.get("order_by"),
            descending=bool(VOLUME_META.get("descending", True)),
            entry_context_mask=prepared.entry_context_mask,
            entry_context_score=prepared.entry_context_score,
            entry_context_weight=prepared.entry_context_weight,
        )
        with prepared.compute_cache.activate(market):
            signals = MatrixStrategyPipeline().run(
                INTRADAY_CANDIDATE_STRATEGY,
                market,
                dict(version.params),
                pipeline_config,
            )
            signals = apply_time_masks(
                signals,
                prepared.entry_time_mask,
                prepared.exit_time_mask,
            )

        sim_market = slice_market_data_matrix(
            market,
            prepared.start_id,
            prepared.stop_id,
        )
        sim_signals = slice_signal_matrix(
            signals,
            prepared.start_id,
            prepared.stop_id,
        )
        minute = _load_candidate_minutes(repo, sim_market, sim_signals)
        confirmed, fill_price, coverage = confirm_intraday_entries(
            sim_market,
            sim_signals,
            minute,
        )
        if not confirmed.any():
            return None, coverage

        confirmed_signals = make_signal_matrix(
            sim_market.shape,
            entry=confirmed,
            exit=sim_signals.exit,
            score=sim_signals.score,
            entry_signal_code=np.where(confirmed, 0, -1).astype(np.int16),
            exit_signal_code=sim_signals.exit_signal_code,
            entry_signal_ids=sim_signals.entry_signal_ids,
            exit_signal_ids=sim_signals.exit_signal_ids,
        )
        matrix = build_market_matrix_from_signals(
            sim_market,
            confirmed_signals,
            entry_delay_bars=0,
            exit_delay_bars=1,
            entry_price_override=fill_price,
        )
        result = service.engine.simulate_market_matrix(
            matrix,
            MatcherConfig(
                entry_fill="close_t",
                exit_fill="open_t+1",
                commission_pct=config.commission_pct,
                stamp_tax_pct=config.stamp_tax_pct,
                slippage_bps=config.slippage_bps,
                stop_loss_pct=-0.06,
                max_hold_days=20,
                max_positions=config.max_positions,
                max_exposure_pct=config.max_exposure_pct,
                initial_capital=config.initial_capital,
            ),
        )
        return result, coverage
    finally:
        prepared.compute_cache.close()


def _sim_result_row(
    version: CapitalUtilizationVersion,
    result,
    coverage: dict[str, Any],
    *,
    config: StrategyBacktestConfig,
) -> dict[str, Any]:
    if result is None:
        return {
            "version": version.id,
            "label": version.label,
            "timing": version.timing,
            "status": "minute_data_unavailable",
            "error": "候选交易日缺少完整14:30及下一分钟K线, 已按规则拒绝交易",
            "stats": {"n_trades": 0},
            "activity": summarize_activity(
                [],
                [],
                initial_capital=config.initial_capital,
                exposure_cap=config.max_exposure_pct,
            ),
            "minute_coverage": coverage,
        }
    trades = [asdict(trade) for trade in result.trades]
    stats = result.stats or {}
    return {
        "version": version.id,
        "label": version.label,
        "timing": version.timing,
        "status": "ok",
        "error": None,
        "stats": {
            key: stats.get(key)
            for key in (
                "total_return",
                "annual_return",
                "max_drawdown",
                "sharpe",
                "sortino",
                "win_rate",
                "profit_factor",
                "n_trades",
                "initial_capital",
                "final_equity",
                "avg_exposure",
                "max_exposure",
            )
        },
        "activity": summarize_activity(
            trades,
            result.equity_curve,
            initial_capital=config.initial_capital,
            exposure_cap=config.max_exposure_pct,
        ),
        "execution": stats.get("execution"),
        "minute_coverage": coverage,
    }


def _combine_sleeves(
    c_result,
    leader_result,
    *,
    reserve_cash: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    sleeves = (("c2", c_result), ("leader", leader_result))
    curve_maps = {
        name: {str(row["date"]): row for row in result.equity_curve}
        for name, result in sleeves
    }
    dates = sorted(set().union(*(set(curve) for curve in curve_maps.values())))
    last_rows = {
        "c2": {"value": 300_000.0, "cash": 300_000.0, "positions": 0, "exposure": 0.0},
        "leader": {"value": 200_000.0, "cash": 200_000.0, "positions": 0, "exposure": 0.0},
    }
    combined_curve: list[dict[str, Any]] = []
    for date_text in dates:
        for name in last_rows:
            if date_text in curve_maps[name]:
                last_rows[name] = curve_maps[name][date_text]
        total_value = reserve_cash + sum(float(row["value"]) for row in last_rows.values())
        invested = sum(
            float(row["value"]) * float(row.get("exposure") or 0.0)
            for row in last_rows.values()
        )
        combined_curve.append({
            "date": date_text,
            "value": round(total_value, 2),
            "cash": round(
                reserve_cash + sum(float(row.get("cash") or 0.0) for row in last_rows.values()),
                2,
            ),
            "positions": sum(int(row.get("positions") or 0) for row in last_rows.values()),
            "exposure": round(invested / total_value if total_value > 0 else 0.0, 4),
        })

    combined_trades: list[dict[str, Any]] = []
    for name, result in sleeves:
        combined_trades.extend({**trade, "sleeve": name} for trade in result.trades)
    combined_trades.sort(key=lambda trade: (str(trade.get("entry_date")), str(trade.get("symbol"))))

    values = np.asarray([float(row["value"]) for row in combined_curve], dtype=float)
    total_return = values[-1] / INITIAL_CAPITAL - 1.0 if values.size else 0.0
    daily = values[1:] / values[:-1] - 1.0 if values.size > 1 else np.array([])
    peaks = np.maximum.accumulate(values) if values.size else np.array([])
    drawdowns = values / peaks - 1.0 if values.size else np.array([])
    annual_return = (
        (1.0 + total_return) ** (252.0 / len(values)) - 1.0
        if values.size and total_return > -1.0
        else total_return
    )
    sharpe = (
        float(np.mean(daily) / np.std(daily) * np.sqrt(252.0))
        if daily.size and np.std(daily) > 0
        else 0.0
    )
    stats = {
        "total_return": round(float(total_return), 4),
        "annual_return": round(float(annual_return), 4),
        "max_drawdown": round(float(drawdowns.min()), 4) if drawdowns.size else 0.0,
        "sharpe": round(sharpe, 2),
        "n_trades": len(combined_trades),
        "initial_capital": INITIAL_CAPITAL,
        "final_equity": round(float(values[-1]), 2) if values.size else INITIAL_CAPITAL,
        "avg_exposure": round(
            float(np.mean([row["exposure"] for row in combined_curve])),
            4,
        ) if combined_curve else 0.0,
        "max_exposure": round(
            max((float(row["exposure"]) for row in combined_curve), default=0.0),
            4,
        ),
    }
    return combined_curve, combined_trades, stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 1, 2))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 8, 17))
    args = parser.parse_args()

    repo = KlineRepository(DataStore())
    service = _service(repo)
    daily_version, minute_version, leader_version = VERSIONS

    daily_config = _config(daily_version, args.start, args.end)
    daily_result = _run_daily(service, daily_config)
    print(json.dumps(_activity_row(
        daily_version,
        daily_result,
        exposure_cap=daily_config.max_exposure_pct,
    ), ensure_ascii=False, default=str), flush=True)

    minute_config = _config(minute_version, args.start, args.end)
    minute_result, coverage = _run_intraday(
        service,
        repo,
        minute_version,
        minute_config,
    )
    print(json.dumps(_sim_result_row(
        minute_version,
        minute_result,
        coverage,
        config=minute_config,
    ), ensure_ascii=False, default=str), flush=True)

    leader_config = _config(leader_version, args.start, args.end)
    leader_result = _run_daily(service, leader_config)
    print(json.dumps(_activity_row(
        leader_version,
        leader_result,
        exposure_cap=leader_config.max_exposure_pct,
    ), ensure_ascii=False, default=str), flush=True)

    c_sleeve_config = _config(
        daily_version,
        args.start,
        args.end,
        initial_capital=300_000.0,
        max_exposure_pct=1.0,
        max_positions=2,
    )
    leader_sleeve_config = _config(
        leader_version,
        args.start,
        args.end,
        initial_capital=200_000.0,
        max_exposure_pct=1.0,
        max_positions=1,
    )
    c_sleeve = _run_daily(service, c_sleeve_config)
    leader_sleeve = _run_daily(service, leader_sleeve_config)
    if c_sleeve.error or leader_sleeve.error:
        combined = {
            "version": "c2_leader_30_20",
            "label": "组合-C2 30% + 龙头20%",
            "status": "error",
            "error": c_sleeve.error or leader_sleeve.error,
        }
    else:
        curve, trades, stats = _combine_sleeves(
            c_sleeve,
            leader_sleeve,
            reserve_cash=500_000.0,
        )
        combined = {
            "version": "c2_leader_30_20",
            "label": "组合-C2 30% + 龙头20%",
            "timing": "固定袖套: C2最多30%, 龙头最多20%",
            "status": "ok",
            "error": None,
            "stats": stats,
            "activity": summarize_activity(
                trades,
                curve,
                initial_capital=INITIAL_CAPITAL,
                exposure_cap=0.50,
            ),
            "sleeves": {
                "c2": summarize_activity(
                    c_sleeve.trades,
                    c_sleeve.equity_curve,
                    initial_capital=300_000.0,
                    exposure_cap=1.0,
                ),
                "leader": summarize_activity(
                    leader_sleeve.trades,
                    leader_sleeve.equity_curve,
                    initial_capital=200_000.0,
                    exposure_cap=1.0,
                ),
            },
        }
    print(json.dumps(combined, ensure_ascii=False, default=str), flush=True)


if __name__ == "__main__":
    main()
