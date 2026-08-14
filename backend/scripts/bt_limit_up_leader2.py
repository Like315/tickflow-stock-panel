# -*- coding: utf-8 -*-
"""龙头打板策略 — 参数敏感性回测（情绪阈值 / 连板门槛 / 持有期 / 对照）。"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from datetime import date
from pathlib import Path

import polars as pl

from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.backtest.engine import BacktestEngine
from app.strategy.engine import StrategyEngine
from app.tickflow.repository import DataStore, KlineRepository

store = DataStore()
repo = KlineRepository(store)
engine = BacktestEngine(repo)
builtin_dir = Path(__file__).resolve().parent.parent / "app" / "strategy" / "builtin"
strategy_engine = StrategyEngine(strategy_dirs=[builtin_dir])
service = StrategyBacktestService(engine=engine, strategy_engine=strategy_engine)

START = date(2025, 11, 3)
END = date(2026, 8, 13)

BASE = dict(
    symbols=None,
    start=START,
    end=END,
    mode="position",
    matching="open_t+1",
    max_positions=4,
    max_exposure_pct=0.8,
    initial_capital=1_000_000.0,
    commission_pct=0.0002,
    stamp_tax_pct=0.0005,
    slippage_bps=5,
)

# 上证指数对照（读 kline_index_daily 手工计算）
bench_df = (
    pl.scan_parquet(r"D:\MyTickFlowStockPanel\data\kline_index_daily\**\*.parquet")
    .filter((pl.col("date") >= START) & (pl.col("date") <= END))
    .sort("date")
    .collect()
)
bc = [r for r in bench_df.iter_rows(named=True) if str(r.get("symbol", "")) == "000001.SH" and r.get("close")]
if len(bc) >= 2:
    bench_ret = bc[-1]["close"] / bc[0]["close"] - 1
else:
    bench_ret = None
print("上证指数区间收益:", f"{bench_ret:.2%}" if bench_ret is not None else "N/A")

CASES = [
    ("无脑打板(对照)", "limit_up_momentum", {"min_boards": 1, "min_change": 5.0}, None),
    ("基准-2板上+情绪80+持1天", "limit_up_leader", {"min_boards": 2, "min_daily_limit_ups": 80, "min_amount_yi": 3.0, "use_emotion_filter": True}, {"max_hold_days": 1}),
    ("情绪更严-阈值100", "limit_up_leader", {"min_boards": 2, "min_daily_limit_ups": 100, "min_amount_yi": 3.0, "use_emotion_filter": True}, {"max_hold_days": 1}),
    ("情绪更松-阈值60", "limit_up_leader", {"min_boards": 2, "min_daily_limit_ups": 60, "min_amount_yi": 3.0, "use_emotion_filter": True}, {"max_hold_days": 1}),
    ("关闭情绪过滤", "limit_up_leader", {"min_boards": 2, "min_daily_limit_ups": 0, "use_emotion_filter": False, "min_amount_yi": 3.0}, {"max_hold_days": 1}),
    ("三板上-只打3连板+", "limit_up_leader", {"min_boards": 3, "min_daily_limit_ups": 80, "min_amount_yi": 3.0, "use_emotion_filter": True}, {"max_hold_days": 1}),
    ("首板也打-1板起", "limit_up_leader", {"min_boards": 1, "min_daily_limit_ups": 80, "min_amount_yi": 3.0, "use_emotion_filter": True}, {"max_hold_days": 1}),
    ("持有2天-给冲高机会", "limit_up_leader", {"min_boards": 2, "min_daily_limit_ups": 80, "min_amount_yi": 3.0, "use_emotion_filter": True}, {"max_hold_days": 2}),
]

out = {}
for name, sid, params, ov in CASES:
    cfg = StrategyBacktestConfig(strategy_id=sid, params=params, overrides=ov, **BASE)
    res = service.run(cfg)
    if res.error:
        print(f"[{name}] ERROR: {res.error}")
        continue
    s = res.stats
    keys = ["total_return", "annual_return", "max_drawdown", "sharpe", "sortino",
            "win_rate", "profit_factor", "n_trades", "avg_pnl", "median_pnl",
            "avg_holding_days", "final_equity"]
    row = {k: s.get(k) for k in keys}
    row["benchmark_return"] = bench_ret
    row["excess"] = (row["total_return"] - bench_ret) if bench_ret is not None and row["total_return"] is not None else None
    row["execution"] = s.get("execution") or {}
    row["equity_curve"] = res.equity_curve
    row["trades"] = res.trades
    out[name] = row
    print(f"===== {name} =====")
    print(json.dumps({k: v for k, v in row.items() if k not in ("execution", "equity_curve", "trades")}, ensure_ascii=False))
    print("exec:", json.dumps(row["execution"], ensure_ascii=False))

with open(r"D:\MyTickFlowStockPanel\output\limit_up_leader_backtest2.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, default=str)
print("\nsaved -> output/limit_up_leader_backtest2.json")
