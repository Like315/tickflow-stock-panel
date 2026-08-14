# -*- coding: utf-8 -*-
"""V5 回测 — 卖出时点(收盘vs次日开盘) / 长假规避 / 换手过滤 / 半样本稳健性。"""
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

V5 = dict(
    min_boards=1, max_boards=3, min_daily_limit_ups=80, min_leader_height=3, min_amount_yi=3.0,
    use_emotion_filter=True, space_leader_only=True, exit_on_not_locked=True,
    use_theme_filter=True, min_theme_limits=5,
    use_premium_filter=False, min_premium_pct=-2.0,
    use_industry_strength=True, min_industry_change_pct=1.0,
    skip_long_gap=True,
)

def run(params, entry_fill="open_t+1", exit_fill="open_t+1", s=START, e=END):
    cfg = StrategyBacktestConfig(
        strategy_id="limit_up_leader_v4", symbols=None, start=s, end=e, params=params, overrides={},
        mode="position", matching="open_t+1", entry_fill=entry_fill, exit_fill=exit_fill,
        max_positions=4, max_exposure_pct=0.8, initial_capital=1_000_000.0,
        commission_pct=0.0002, stamp_tax_pct=0.0005, slippage_bps=5,
    )
    return service.run(cfg)

CASES = [
    ("V5基线(次日开盘卖+长假规避)", "open_t+1", "open_t+1", dict(V5)),
    ("V5收盘卖(确认不板当天收盘走)", "open_t+1", "close_t", dict(V5)),
    ("V5收盘卖+关闭长假规避", "open_t+1", "close_t", {**V5, "skip_long_gap": False}),
    ("V5收盘卖+换手≥3%", "open_t+1", "close_t", {**V5, "min_turnover_pct": 3.0}),
]

out = {}
for name, ef, xf, params in CASES:
    r = run(params, ef, xf)
    st = r.stats
    if st.get("total_return") is None:
        print(f"[{name}] ERROR: {r.error}")
        continue
    trades = r.trades
    wins = [t for t in trades if t.get("pnl_pct", 0) > 0]
    losses = [t for t in trades if t.get("pnl_pct", 0) <= 0]
    sl = [t for t in trades if t.get("exit_reason") == "stop_loss"]
    sig = [t for t in trades if t.get("exit_reason") == "signal"]
    row = {
        "total_return": st.get("total_return"), "max_drawdown": st.get("max_drawdown"),
        "win_rate": st.get("win_rate"), "n_trades": st.get("n_trades"), "avg_pnl": st.get("avg_pnl"),
        "median_pnl": st.get("median_pnl"), "avg_holding_days": st.get("avg_holding_days"),
        "profit_factor": st.get("profit_factor"),
        "execution": st.get("execution") or {}, "equity_curve": r.equity_curve, "trades": trades,
    }
    out[name] = row
    print(f"===== {name} =====")
    print(f"  total={st.get('total_return'):.2%} md={st.get('max_drawdown'):.2%} win={st.get('win_rate'):.1%} n={st.get('n_trades')} avg={st.get('avg_pnl'):.2%}")
    if trades:
        sl_avg = sum(t["pnl_pct"] for t in sl) / len(sl) if sl else 0
        sig_avg = sum(t["pnl_pct"] for t in sig) / len(sig) if sig else 0
        print(f"  盈{len(wins)}均{sum(t['pnl_pct'] for t in wins)/max(len(wins),1):.2%} | 亏{len(losses)}均{sum(t['pnl_pct'] for t in losses)/max(len(losses),1):.2%} | 止损{len(sl)}笔均{sl_avg:.2%} | 不板卖{len(sig)}笔均{sig_avg:.2%}")

with open(r"D:\MyTickFlowStockPanel\output\limit_up_leader_v5_backtest.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, default=str)
print("\nsaved v5")

# 半样本稳健性：最优配置（收盘卖）在前半/后半
print("\n===== 半样本稳健性（收盘卖配置）=====")
P1 = (date(2025, 11, 3), date(2026, 2, 27))
P2 = (date(2026, 3, 2), date(2026, 8, 13))
for tag, (s, e) in [("前半(2025-11~2026-02)", P1), ("后半(2026-03~08)", P2)]:
    r = run(dict(V5), "open_t+1", "close_t", s, e)
    st = r.stats
    print(f"  {tag}: total={st.get('total_return'):.2%} n={st.get('n_trades')} win={st.get('win_rate'):.1%} md={st.get('max_drawdown'):.2%}" if st.get("total_return") is not None else f"  {tag}: ERR {r.error}")
