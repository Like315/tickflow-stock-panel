# -*- coding: utf-8 -*-
"""龙头打板 v4 回测 — 溢价温度计 / 行业强度 / 与 v3 最优对比 + 逐笔复盘数据导出。"""
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

# v3 最优 = 行业≥5 + 空间龙（对照）
V3_OPT = dict(
    min_boards=1, min_daily_limit_ups=80, min_leader_height=3, min_amount_yi=3.0,
    use_emotion_filter=True, space_leader_only=True, exit_on_not_locked=True,
    use_theme_filter=True, theme_source="hy", min_theme_limits=5, theme_leader_only=False,
)

CASES = [
    ("v3最优(行业5+空间龙)(对照)", "limit_up_leader_v3", dict(V3_OPT), {}, "上轮最优基线"),
    ("v4全开(行业5+强度1%+溢价-2%)", "limit_up_leader_v4",
     {**V3_OPT, "use_premium_filter": True, "min_premium_pct": -2.0,
      "use_industry_strength": True, "min_industry_change_pct": 1.0}, {}, "v4 完整版"),
    ("v4关溢价(只开行业强度)", "limit_up_leader_v4",
     {**V3_OPT, "use_premium_filter": False,
      "use_industry_strength": True, "min_industry_change_pct": 1.0}, {}, "验证溢价贡献"),
    ("v4关强度(只开溢价)", "limit_up_leader_v4",
     {**V3_OPT, "use_premium_filter": True, "min_premium_pct": -2.0,
      "use_industry_strength": False}, {}, "验证行业强度贡献"),
    ("v4溢价更严(-1%)", "limit_up_leader_v4",
     {**V3_OPT, "use_premium_filter": True, "min_premium_pct": -1.0,
      "use_industry_strength": True, "min_industry_change_pct": 1.0}, {}, "溢价门槛提高"),
    ("v4溢价更严(0%)", "limit_up_leader_v4",
     {**V3_OPT, "use_premium_filter": True, "min_premium_pct": 0.0,
      "use_industry_strength": True, "min_industry_change_pct": 1.0}, {}, "昨日涨停池今日不亏才做"),
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

with open(r"D:\MyTickFlowStockPanel\output\limit_up_leader_v4_backtest.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, default=str)

# 逐笔复盘：导出 v4 全开版的全部交易明细
best = out.get("v4全开(行业5+强度1%+溢价-2%)")
if best and "trades" in best and best["trades"]:
    trades = best["trades"]
    json.dump(trades, open(r"D:\MyTickFlowStockPanel\output\limit_up_leader_v4_trades.json", "w", encoding="utf-8"), ensure_ascii=False, default=str)
    wins = [t for t in trades if t.get("pnl_pct", 0) > 0]
    losses = [t for t in trades if t.get("pnl_pct", 0) <= 0]
    print(f"\n逐笔复盘: 总{len(trades)}笔, 盈{len(wins)} 亏{len(losses)}")
    print(f"盈利笔均: {sum(t['pnl_pct'] for t in wins)/max(len(wins),1):.2%} | 亏损笔均: {sum(t['pnl_pct'] for t in losses)/max(len(losses),1):.2%}")
    # 按退出原因分析
    by_reason = {}
    for t in trades:
        by_reason.setdefault(t.get("exit_reason"), []).append(t)
    for reason, ts in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        avg = sum(t["pnl_pct"] for t in ts) / len(ts)
        print(f"  退出方式 {reason}: {len(ts)}笔, 平均 {avg:.2%}")
    print("\n亏损笔明细（前12）:")
    for t in sorted(losses, key=lambda x: x.get("pnl_pct", 0))[:12]:
        print(f"  {t.get('exit_date')} {t.get('name')}({t.get('symbol')}) 入{t.get('entry_date')} {t.get('entry_price')}→{t.get('exit_price')} {t.get('pnl_pct'):.2%} {t.get('exit_reason')}")

print("\nsaved -> output/limit_up_leader_v4_backtest.json")
