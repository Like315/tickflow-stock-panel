"""尾盘首阳·10分钟 MA5 拐头策略。"""

import numpy as np
from _late_day_first_bullish import replay_intraday_strategy  # type: ignore[import-not-found]

from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix, valid_shift

# StrategyEngine loads built-ins as isolated modules and temporarily exposes this directory.

META = {
    "id": "late_day_first_bullish_ma5_turn",
    "name": "尾盘首阳·10分钟MA5拐头",
    "description": "前一日弱势准备，次日14:30后在涨幅候选池确认首阳与10分钟MA5刚上扬",
    "tags": ["首阳", "尾盘", "反转", "10分钟", "次日早盘"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [
        {
            "id": "prior_bearish_days",
            "label": "前置弱势天数",
            "type": "int",
            "default": 1,
            "min": 1,
            "max": 3,
            "step": 1,
        },
        {
            "id": "gainer_rank_limit",
            "label": "涨幅候选池排名上限",
            "type": "int",
            "default": 20,
            "min": 5,
            "max": 50,
            "step": 5,
        },
        {
            "id": "minimum_change_pct",
            "label": "尾盘最低涨幅",
            "type": "float",
            "default": 0.01,
            "min": 0.0,
            "max": 0.08,
            "step": 0.005,
        },
        {
            "id": "ma5_min_slope_pct",
            "label": "10分钟MA5最低斜率",
            "type": "float",
            "default": 0.0,
            "min": 0.0,
            "max": 0.01,
            "step": 0.0005,
        },
        {
            "id": "morning_take_profit_pct",
            "label": "次日早盘止盈",
            "type": "float",
            "default": 0.03,
            "min": 0.01,
            "max": 0.10,
            "step": 0.005,
        },
        {
            "id": "morning_trailing_activate_pct",
            "label": "早盘回撤止盈启动",
            "type": "float",
            "default": 0.015,
            "min": 0.005,
            "max": 0.05,
            "step": 0.005,
        },
        {
            "id": "morning_trailing_drawdown_pct",
            "label": "早盘高点回撤",
            "type": "float",
            "default": 0.008,
            "min": 0.003,
            "max": 0.03,
            "step": 0.001,
        },
        {
            "id": "morning_stop_loss_pct",
            "label": "次日早盘止损",
            "type": "float",
            "default": -0.03,
            "min": -0.10,
            "max": -0.01,
            "step": 0.005,
        },
    ],
    "scoring": {"change_pct": 0.4, "momentum_20d": 0.6},
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_late_day_first_bullish_setup"]
EXIT_SIGNALS = ["signal_next_morning_managed_exit"]
STOP_LOSS = -0.03
MIN_HOLD_DAYS = 1
MAX_HOLD_DAYS = 2
ALERTS: list[dict[str, object]] = []
INTRADAY_REPLAY = replay_intraday_strategy


class LateDayFirstBullishSetupStrategy:
    """仅使用已完成日线生成下一交易日的分钟确认准备池。"""

    def required_fields(self) -> frozenset[str]:
        """声明准备信号只依赖日线开盘价和收盘价。"""
        return frozenset({"open", "close"})

    def required_warmup_bars(self, params: dict) -> int:
        """返回覆盖可优化前置弱势天数的预热长度。"""
        return max(5, int(params.get("prior_bearish_days", 1)) + 1)

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        """在信号日收盘后标记连续收阴或平盘的准备信号。"""
        bearish_days = max(1, min(int(params.get("prior_bearish_days", 1)), 3))
        setup = np.ones(market.shape, dtype=bool)
        for offset in range(bearish_days):
            setup &= valid_shift(market.close, offset) <= valid_shift(market.open, offset)
        return make_signal_matrix(
            market.shape,
            entry=setup.astype(np.uint8),
            exit=np.zeros(market.shape, dtype=np.uint8),
            entry_signal_code=np.where(setup, 0, -1).astype(np.int16),
            exit_signal_code=np.full(market.shape, -1, dtype=np.int16),
            entry_signal_ids=("signal_late_day_first_bullish_setup",),
            exit_signal_ids=("signal_next_morning_managed_exit",),
        )


MATRIX_STRATEGY = LateDayFirstBullishSetupStrategy()
