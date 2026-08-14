# -*- coding: utf-8 -*-
"""龙头打板 v3 回测 — 板块效应与板块内龙头贡献验证。"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from datetime import date
from pathlib import Path

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

V2 = dict(
    min_boards=2, min_daily_limit_ups=80, min_leader_height=3, min_amount_yi=3.0,
    use_emotion_filter=True, space_leader_only=False, exit_on_not_locked=True,
)

CASES = [
    ("v2基准(对照)", "limit_up_leader_v2", dict(V2), {}, "上一版基线"),
    ("v3概念板块效应(≥3家)", "limit_up_leader_v3",
     {**V2, "use_theme_filter": True, "theme_source": "gn", "min_theme_limits": 3, "theme_leader_only": False},
     {}, "概念板块合力过滤"),
    ("v3概念板块效应(≥5家)", "limit_up_leader_v3",
     {**V2, "use_theme_filter": True, "theme_source": "gn", "min_theme_limits": 5, "theme_leader_only": False},
     {}, "更严板块合力"),
    ("v3行业板块效应(≥5家)", "limit_up_leader_v3",
     {**V2, "use_theme_filter": True, "theme_source": "hy", "min_theme_limits": 5, "theme_leader_only": False},
     {}, "行业一级板块合力"),
    ("v3概念+板块内龙头", "limit_up_leader_v3",
     {**V2, "use_theme_filter": True, "theme_source": "gn", "min_theme_limits": 3, "theme_leader_only": True},
     {}, "板块内最高板才买"),
    ("v3空间龙+概念+龙头", "limit_up_leader_v3",
     {**V2, "space_leader_only": True, "use_theme_filter": True, "theme_source": "gn",
      "min_theme_limits": 3, "theme_leader_only": True},
     {}, "最严格组合"),
]

out = {}
for name, sid, params, ov, note in CASES:
    cfg = StrategyBacktestConfig(strategy_id=sid, params=params, overrides=ov, **BASE)
    res = service.run(cfg)
    if res.error:
        print(f"[{name}] ERROR: {res.error}")
        out[name] = {"error": res.error}
        continue
    s = res.stats
    row = {
        "total_return": s.get("total_return"),
        "max_drawdown": s.get("max_drawdown"),
        "win_rate": s.get("win_rate"),
        "n_trades": s.get("n_trades"),
        "avg_pnl": s.get("avg_pnl"),
        "median_pnl": s.get("median_pnl"),
        "avg_holding_days": s.get("avg_holding_days"),
        "profit_factor": s.get("profit_factor"),
        "execution": s.get("execution") or {},
        "equity_curve": res.equity_curve,
        "trades": res.trades,
        "note": note,
    }
    out[name] = row
    print(f"===== {name} | {note} =====")
    print(json.dumps({k: row[k] for k in ["total_return", "max_drawdown", "win_rate", "n_trades",
                                           "avg_pnl", "median_pnl", "avg_holding_days", "profit_factor"]}, ensure_ascii=False))
    print("exec:", json.dumps(row["execution"], ensure_ascii=False))

with open(r"D:\MyTickFlowStockPanel\output\limit_up_leader_v3_backtest.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, default=str)
print("\nsaved -> output/limit_up_leader_v3_backtest.json")
