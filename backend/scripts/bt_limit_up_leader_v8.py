# -*- coding: utf-8 -*-
"""v8 回测 — 科技子题材轮动 + 小资金支持。"""
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

def run(sid, params, capital=1_000_000.0, s=START, e=END):
    cfg = StrategyBacktestConfig(
        strategy_id=sid, symbols=None, start=s, end=e, params=params, overrides={},
        mode="position", matching="open_t+1", entry_fill="open_t+1", exit_fill="open_t+1",
        max_positions=4, max_exposure_pct=0.8, initial_capital=capital,
        commission_pct=0.0002, stamp_tax_pct=0.0005, slippage_bps=5,
    )
    return service.run(cfg)

V6 = dict(
    tech_filter=True,
    tech_keywords="人工智能;AI;AIGC;大模型;ChatGPT;DeepSeek;芯片;半导体;集成电路;存储芯片;先进封装;光刻机;算力;服务器;液冷;CPO;光模块;数据中心;IDC;机器人;人形机器人;机器视觉;消费电子;华为概念;苹果概念;折叠屏;智能穿戴;国产软件;信创;操作系统;数据库;数据要素;数字经济;云计算;大数据;5G;6G;卫星互联网;卫星导航;PCB;GPU;智能驾驶;无人驾驶;车联网;低空经济",
    use_index_filter=True, index_symbol="000001.SH", index_mode="ma20", min_tech_share=0.3,
    min_boards=1, max_boards=3, min_daily_limit_ups=80, min_leader_height=3, min_amount_yi=3.0,
    use_emotion_filter=True, space_leader_only=True, exit_on_not_locked=True,
    use_theme_filter=True, min_theme_limits=5, use_industry_strength=True, min_industry_change_pct=1.0,
    skip_long_gap=True,
)

V8_BASE = dict(
    subtheme_mode="top1", subtheme_min_limits=3, max_price=0.0,
    use_index_filter=True, index_symbol="000001.SH", index_mode="ma20", min_tech_share=0.3,
    min_boards=1, max_boards=3, min_daily_limit_ups=80, min_leader_height=3, min_amount_yi=3.0,
    use_emotion_filter=True, space_leader_only=True, exit_on_not_locked=True,
    use_theme_filter=True, min_theme_limits=5, use_industry_strength=True, min_industry_change_pct=1.0,
    skip_long_gap=True,
)

CASES = [
    ("v6对照(全科技池,100万)", "limit_up_leader_v6", dict(V6), 1_000_000.0),
    ("v8轮动top1(100万)", "limit_up_leader_v8", {**V8_BASE, "subtheme_mode": "top1"}, 1_000_000.0),
    ("v8轮动any(100万)", "limit_up_leader_v8", {**V8_BASE, "subtheme_mode": "any"}, 1_000_000.0),
    ("v8轮动top1+小资金10万+股价≤50", "limit_up_leader_v8", {**V8_BASE, "subtheme_mode": "top1", "max_price": 50.0}, 100_000.0),
    ("v8轮动top1+小资金10万+股价≤100", "limit_up_leader_v8", {**V8_BASE, "subtheme_mode": "top1", "max_price": 100.0}, 100_000.0),
    ("v8轮动top1+小资金20万+股价≤50", "limit_up_leader_v8", {**V8_BASE, "subtheme_mode": "top1", "max_price": 50.0}, 200_000.0),
]

out = {}
for name, sid, params, capital in CASES:
    r = run(sid, params, capital)
    st = r.stats
    if st.get("total_return") is None:
        print(f"[{name}] ERROR: {r.error}")
        out[name] = {"error": r.error}
        continue
    trades = r.trades
    wins = [t for t in trades if t.get("pnl_pct", 0) > 0]
    losses = [t for t in trades if t.get("pnl_pct", 0) <= 0]
    ex = st.get("execution") or {}
    row = {
        "total_return": st.get("total_return"), "max_drawdown": st.get("max_drawdown"),
        "win_rate": st.get("win_rate"), "n_trades": st.get("n_trades"), "avg_pnl": st.get("avg_pnl"),
        "median_pnl": st.get("median_pnl"), "avg_holding_days": st.get("avg_holding_days"),
        "profit_factor": st.get("profit_factor"), "final_equity": st.get("final_equity"),
        "execution": ex, "equity_curve": r.equity_curve, "trades": trades,
    }
    out[name] = row
    print(f"===== {name} =====")
    print(f"  total={st.get('total_return'):.2%} md={st.get('max_drawdown'):.2%} win={st.get('win_rate'):.1%} n={st.get('n_trades')} avg={st.get('avg_pnl'):.2%} final={st.get('final_equity'):,.0f}")
    print(f"  盈{len(wins)}均{sum(t['pnl_pct'] for t in wins)/max(len(wins),1):.2%} | 亏{len(losses)}均{sum(t['pnl_pct'] for t in losses)/max(len(losses),1):.2%} | buy_lot_size={ex.get('buy_lot_size', 0)} buy_cash={ex.get('buy_cash', 0)}")
    if trades:
        for t in trades:
            print(f"    {t.get('entry_date')}→{t.get('exit_date')} {t.get('name')} 价{t.get('entry_price')} {t.get('pnl_pct'):+.2%} {t.get('exit_reason')}")

with open(r"D:\MyTickFlowStockPanel\output\limit_up_leader_v8_backtest.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, default=str)
print("\nsaved v8")
