# -*- coding: utf-8 -*-
"""v6 科技主线回测 — 科技池 / 指数过滤 / 题材集中度 与 V5 最优对比。"""
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
    mode="position", matching="open_t+1",
    entry_fill="open_t+1", exit_fill="open_t+1",
    max_positions=4, max_exposure_pct=0.8, initial_capital=1_000_000.0,
    commission_pct=0.0002, stamp_tax_pct=0.0005, slippage_bps=5,
)

def run(sid, params, s=START, e=END):
    cfg = StrategyBacktestConfig(strategy_id=sid, symbols=None, start=s, end=e, params=params, overrides={}, **BASE)
    return service.run(cfg)

V5_OPT = dict(
    min_boards=1, max_boards=3, min_daily_limit_ups=80, min_leader_height=3, min_amount_yi=3.0,
    use_emotion_filter=True, space_leader_only=True, exit_on_not_locked=True,
    use_theme_filter=True, min_theme_limits=5,
    use_premium_filter=False, use_industry_strength=True, min_industry_change_pct=1.0,
    skip_long_gap=True,
)

# v6 默认（科技 + 指数 + 集中度）
V6 = dict(
    tech_filter=True,
    tech_keywords="人工智能;AI;AIGC;大模型;ChatGPT;DeepSeek;芯片;半导体;集成电路;存储芯片;先进封装;光刻机;算力;服务器;液冷;CPO;光模块;数据中心;IDC;机器人;人形机器人;机器视觉;消费电子;华为概念;苹果概念;折叠屏;智能穿戴;国产软件;信创;操作系统;数据库;数据要素;数字经济;云计算;大数据;5G;6G;卫星互联网;卫星导航;PCB;GPU;智能驾驶;无人驾驶;车联网;低空经济",
    use_index_filter=True, index_symbol="000001.SH", index_mode="ma20",
    min_tech_share=0.3,
    **V5_OPT,
)

def run(sid, params, s=START, e=END):
    cfg = StrategyBacktestConfig(strategy_id=sid, symbols=None, start=s, end=e, params=params, overrides={}, **BASE)
    return service.run(cfg)

CASES = [
    ("V5最优(全市场)(对照)", "limit_up_leader_v4", dict(V5_OPT), "上轮最优基线"),
    ("v6科技池(无指数/无集中度)", "limit_up_leader_v6", {**V6, "use_index_filter": False, "min_tech_share": 0.0}, "科技聚焦单维度"),
    ("v6科技+上证MA20", "limit_up_leader_v6", {**V6, "min_tech_share": 0.0}, "加大盘过滤"),
    ("v6科技+MA20+集中度30%", "limit_up_leader_v6", dict(V6), "v6 完整版"),
    ("v6科技+MA20+集中度40%", "limit_up_leader_v6", {**V6, "min_tech_share": 0.4}, "题材更聚焦"),
    ("v6科技+科创50过滤", "limit_up_leader_v6", {**V6, "index_symbol": "000688.SH"}, "用科创50做大盘参考"),
]

out = {}
for name, sid, params, note in CASES:
    r = run(sid, params)
    st = r.stats
    if st.get("total_return") is None:
        print(f"[{name}] ERROR: {r.error}")
        out[name] = {"error": r.error}
        continue
    row = {
        "total_return": st.get("total_return"), "max_drawdown": st.get("max_drawdown"),
        "win_rate": st.get("win_rate"), "n_trades": st.get("n_trades"), "avg_pnl": st.get("avg_pnl"),
        "median_pnl": st.get("median_pnl"), "avg_holding_days": st.get("avg_holding_days"),
        "profit_factor": st.get("profit_factor"),
        "execution": st.get("execution") or {}, "equity_curve": r.equity_curve, "trades": r.trades, "note": note,
    }
    out[name] = row
    trades = r.trades
    wins = [t for t in trades if t.get("pnl_pct", 0) > 0]
    losses = [t for t in trades if t.get("pnl_pct", 0) <= 0]
    print(f"===== {name} | {note} =====")
    print(f"  total={st.get('total_return'):.2%} md={st.get('max_drawdown'):.2%} win={st.get('win_rate'):.1%} n={st.get('n_trades')} avg={st.get('avg_pnl'):.2%} pf={st.get('profit_factor')}")
    if trades:
        print(f"  盈{len(wins)}均{sum(t['pnl_pct'] for t in wins)/max(len(wins),1):.2%} | 亏{len(losses)}均{sum(t['pnl_pct'] for t in losses)/max(len(losses),1):.2%}")

with open(r"D:\MyTickFlowStockPanel\output\limit_up_leader_v6_backtest.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, default=str)
print("\nsaved v6")

# 子区间
print("\n===== v6 完整版 子区间 =====")
P1 = (date(2025, 11, 3), date(2026, 2, 27))
P2 = (date(2026, 3, 2), date(2026, 8, 13))
for tag, (s, e) in [("前半(2025-11~2026-02)", P1), ("后半(2026-03~08)", P2)]:
    r = run("limit_up_leader_v6", dict(V6), s, e)
    st = r.stats
    print(f"  {tag}: total={st.get('total_return'):.2%} n={st.get('n_trades')} win={st.get('win_rate'):.1%}" if st.get("total_return") is not None else f"  {tag}: ERR {r.error}")
