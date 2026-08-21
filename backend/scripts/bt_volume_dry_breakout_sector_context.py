"""Compare three volume-dry-breakout presets with close-T sector overlays."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np

from app.backtest.engine import BacktestEngine
from app.backtest.strategy import (
    PreparedMatrixBacktest,
    StrategyBacktestConfig,
    StrategyBacktestService,
)
from app.strategy.engine import StrategyEngine
from app.strategy.research.volume_dry_breakout_versions import (
    VERSIONS,
    VolumeDryBreakoutVersion,
)
from app.tickflow.repository import DataStore, KlineRepository


def _config(
    start: date,
    end: date,
    version: VolumeDryBreakoutVersion,
    max_positions: int | None,
    context_lag_bars: int | None,
) -> StrategyBacktestConfig:
    overrides = dict(version.overrides)
    context = overrides.get("sector_context_filter")
    if isinstance(context, dict) and context_lag_bars is not None:
        overrides["sector_context_filter"] = {
            **context,
            "lag_bars": context_lag_bars,
        }
    effective_max_positions = version.max_positions if max_positions is None else max_positions
    return StrategyBacktestConfig(
        strategy_id="volume_dry_breakout",
        symbols=None,
        start=start,
        end=end,
        params=version.params,
        overrides=overrides,
        mode="position",
        matching="open_t+1",
        entry_fill="open_t+1",
        exit_fill="open_t+1",
        max_positions=effective_max_positions,
        max_exposure_pct=version.max_exposure_pct,
        initial_capital=1_000_000.0,
        commission_pct=0.0002,
        stamp_tax_pct=0.0005,
        slippage_bps=5,
    )


def _without_entry_range(
    prepared: PreparedMatrixBacktest,
    start: date,
    end: date,
) -> tuple[PreparedMatrixBacktest, int]:
    """Disable new entries in a date range while preserving prices and exits."""
    if end < start:
        raise ValueError("skip entry end must not be earlier than start")
    start_text = str(start)
    end_text = str(end)
    skipped_rows = np.fromiter(
        (start_text <= label[:10] <= end_text for label in prepared.market_data.timestamp_labels),
        dtype=bool,
        count=len(prepared.market_data.timestamp_labels),
    )
    skipped_trading_days = int(np.count_nonzero(prepared.entry_time_mask & skipped_rows))
    entry_time_mask = prepared.entry_time_mask & ~skipped_rows
    entry_time_mask.flags.writeable = False
    return replace(prepared, entry_time_mask=entry_time_mask), skipped_trading_days


def _trades_touching_range(trades: list[dict], start: date, end: date) -> list[dict]:
    start_text = str(start)
    end_text = str(end)
    return [
        {
            "symbol": trade.get("symbol"),
            "entry_date": trade.get("entry_date"),
            "exit_date": trade.get("exit_date"),
            "pnl_pct": trade.get("pnl_pct"),
        }
        for trade in trades
        if str(trade.get("entry_date", "")) <= end_text
        and str(trade.get("exit_date", "")) >= start_text
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 1, 2))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 8, 17))
    parser.add_argument(
        "--max-positions",
        type=int,
        default=None,
        help="Override preset slots (A/B=10, C=3)",
    )
    parser.add_argument(
        "--context-lag-bars",
        type=int,
        choices=(0, 1),
        default=None,
        help="Override B/C context lag: 0=close T (default), 1=T-1 control",
    )
    parser.add_argument("--skip-entry-start", type=date.fromisoformat)
    parser.add_argument("--skip-entry-end", type=date.fromisoformat)
    args = parser.parse_args()
    if (args.skip_entry_start is None) != (args.skip_entry_end is None):
        parser.error("--skip-entry-start and --skip-entry-end must be used together")

    repo = KlineRepository(DataStore())
    service = StrategyBacktestService(
        engine=BacktestEngine(repo),
        strategy_engine=StrategyEngine(
            strategy_dirs=[Path(__file__).resolve().parent.parent / "app" / "strategy" / "builtin"]
        ),
    )

    for version in VERSIONS:
        config = _config(
            args.start,
            args.end,
            version,
            args.max_positions,
            args.context_lag_bars,
        )
        prepared = service.prepare_matrix_optimization([config])
        skipped_trading_days = 0
        if args.skip_entry_start is not None and args.skip_entry_end is not None:
            prepared, skipped_trading_days = _without_entry_range(
                prepared,
                args.skip_entry_start,
                args.skip_entry_end,
            )
        try:
            result = service.run(config, prepared=prepared)
            stats = result.stats or {}
            trades_touching_skip_range = []
            if args.skip_entry_start is not None and args.skip_entry_end is not None:
                trades_touching_skip_range = _trades_touching_range(
                    result.trades,
                    args.skip_entry_start,
                    args.skip_entry_end,
                )
            row = {
                "version": version.id,
                "label": version.label,
                "start": str(args.start),
                "end": str(args.end),
                "max_exposure_pct": version.max_exposure_pct,
                "max_positions": config.max_positions,
                "skip_entry_start": (str(args.skip_entry_start) if args.skip_entry_start else None),
                "skip_entry_end": (str(args.skip_entry_end) if args.skip_entry_end else None),
                "skipped_trading_days": skipped_trading_days,
                "trades_touching_skip_range": trades_touching_skip_range,
                "error": result.error,
                "total_return": stats.get("total_return"),
                "annual_return": stats.get("annual_return"),
                "max_drawdown": stats.get("max_drawdown"),
                "sharpe": stats.get("sharpe"),
                "win_rate": stats.get("win_rate"),
                "n_trades": stats.get("n_trades"),
                "profit_factor": stats.get("profit_factor"),
                "initial_capital": stats.get("initial_capital"),
                "final_equity": stats.get("final_equity"),
                "avg_exposure": stats.get("avg_exposure"),
                "max_exposure": stats.get("max_exposure"),
                "sector_context": stats.get("sector_context_filter"),
            }
            print(json.dumps(row, ensure_ascii=False, default=str), flush=True)
        finally:
            prepared.compute_cache.close()


if __name__ == "__main__":
    main()
