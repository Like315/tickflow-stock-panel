# -*- coding: utf-8 -*-
"""龙头打板策略回测 — 默认参数 + 参数敏感性对比。

防作弊口径（全部沿用现有引擎，审计结论见报告）：
  - 成交: open_t+1（T+1 开盘价），信号只用 T 日收盘数据
  - 一字涨停买不进 / 跌停卖不出 / 停牌: 引擎拦截或挂起顺延
  - 成本: 佣金万2双边 + 印花税卖出0.05% + 滑点5bps
  - warmup 数据只算特征不参与交易
"""
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

CASES = [
    # (名称, strategy_id, params, 备注)
    ("无脑打板(对照)", "limit_up_momentum", {"min_boards": 1, "min_change": 5.0}, "现有策略默认: 涨停即买"),
    ("龙头打板-基准", "limit_up_leader", {"min_boards": 2, "min_daily_limit_ups": 30, "min_amount_yi": 3.0, "use_emotion_filter": True}, "二板起+情绪过滤+成交额"),
    ("龙头打板-保守", "limit_up_leader", {"min_boards": 2, "min_daily_limit_ups": 50, "min_amount_yi": 5.0, "use_emotion_filter": True}, "更严情绪+流动性"),
    ("龙头打板-激进", "limit_up_leader", {"min_boards": 1, "min_daily_limit_ups": 20, "min_amount_yi": 2.0, "use_emotion_filter": True}, "首板也打, 情绪门槛低"),
    ("龙头打板-无情绪过滤", "limit_up_leader", {"min_boards": 2, "use_emotion_filter": False, "min_amount_yi": 3.0}, "验证情绪过滤贡献"),
]

out = {}
for name, sid, params, _note in CASES:
    cfg = StrategyBacktestConfig(strategy_id=sid, params=params, **BASE)
    res = service.run(cfg)
    if res.error:
        print(f"[{name}] ERROR: {res.error}")
        continue
    s = res.stats
    print(f"===== {name} =====")
    print(json.dumps({k: s.get(k) for k in [
        "total_return", "annual_return", "max_drawdown", "sharpe", "sortino",
        "win_rate", "profit_factor", "n_trades", "avg_pnl", "avg_win", "avg_loss",
        "median_pnl", "avg_holding_days", "final_equity", "benchmark_return", "excess",
    ]}, ensure_ascii=False))
    exec_ = s.get("execution") or {}
    print("execution:", json.dumps(exec_, ensure_ascii=False))
    out[name] = {
        "stats": {k: s.get(k) for k in ["total_return", "annual_return", "max_drawdown", "sharpe",
                                         "sortino", "win_rate", "profit_factor", "n_trades",
                                         "avg_pnl", "median_pnl", "avg_holding_days", "final_equity",
                                         "benchmark_return", "excess"]},
        "execution": exec_,
        "equity_curve": res.equity_curve,
        "trades": res.trades,
    }

with open(r"D:\MyTickFlowStockPanel\output\limit_up_leader_backtest.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, default=str)
print("\nsaved -> output/limit_up_leader_backtest.json")
