"""验证矩阵原生回测管线可运行（现有策略 limit_up_momentum）。"""

import io
import json
import sys
from datetime import date
from pathlib import Path

from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.strategy.engine import StrategyEngine
from app.tickflow.repository import DataStore, KlineRepository

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

store = DataStore()
repo = KlineRepository(store)
engine = BacktestEngine(repo)
builtin_dir = Path(__file__).resolve().parent.parent / "app" / "strategy" / "builtin"
strategy_engine = StrategyEngine(strategy_dirs=[builtin_dir])
service = StrategyBacktestService(engine=engine, strategy_engine=strategy_engine)

config = StrategyBacktestConfig(
    strategy_id="limit_up_momentum",
    symbols=None,
    start=date(2025, 10, 1),
    end=date(2026, 6, 30),
    mode="position",
    matching="open_t+1",
    max_positions=5,
    max_exposure_pct=0.6,
    initial_capital=1_000_000.0,
)
res = service.run(config)
print("error:", res.error)
if res.error is None:
    print("stats:", json.dumps(res.stats, ensure_ascii=False, default=str)[:3000])
    print("n_trades:", len(res.trades))
    if res.trades:
        print("first trade:", json.dumps(res.trades[0], ensure_ascii=False, default=str))
    print("equity points:", len(res.equity_curve))
