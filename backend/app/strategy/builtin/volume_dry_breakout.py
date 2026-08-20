"""缩量破高确认 - 高量小实体后缩量突破前高。"""

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    make_signal_matrix,
    matrix_feature,
)
from app.backtest.matrix import (
    valid_shift as shift,
)

META = {
    "id": "volume_dry_breakout",
    "name": "缩量破高确认(实验)",
    "description": "高量长下影换手后缩量收盘突破前高",
    "tags": ["量价", "缩量", "突破", "实验"],
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "params": [
        {
            "id": "setup_vol_ratio_min",
            "label": "前日最低量比",
            "type": "float",
            "default": 2.0,
            "min": 1.2,
            "max": 5.0,
            "step": 0.1,
        },
        {
            "id": "max_body_to_range",
            "label": "前日最大实体占振幅",
            "type": "float",
            "default": 0.35,
            "min": 0.05,
            "max": 0.8,
            "step": 0.05,
        },
        {
            "id": "min_lower_wick_to_range",
            "label": "前日最小下影占振幅",
            "type": "float",
            "default": 0.35,
            "min": 0.0,
            "max": 0.8,
            "step": 0.05,
        },
        {
            "id": "confirm_volume_ratio_max",
            "label": "确认日相对前日最大量比",
            "type": "float",
            "default": 0.8,
            "min": 0.2,
            "max": 1.2,
            "step": 0.05,
        },
        {
            "id": "require_bullish_confirm",
            "label": "要求确认日收阳",
            "type": "bool",
            "default": True,
        },
        {
            "id": "require_above_ma20",
            "label": "要求确认日位于MA20上方",
            "type": "bool",
            "default": True,
        },
        {
            "id": "use_extension_filter",
            "label": "限制偏离MA20",
            "type": "bool",
            "default": True,
        },
        {
            "id": "ma20_bias_max",
            "label": "最大MA20上方偏离",
            "type": "float",
            "default": 0.12,
            "min": 0.02,
            "max": 0.5,
            "step": 0.01,
        },
        {
            "id": "use_breakout_quality_guard",
            "label": "过滤高位浅突破",
            "type": "bool",
            "default": False,
        },
        {
            "id": "breakout_guard_ma20_bias_min",
            "label": "高位浅突破MA20偏离下限",
            "type": "float",
            "default": 0.05,
            "min": 0.02,
            "max": 0.15,
            "step": 0.01,
        },
        {
            "id": "breakout_guard_margin_max",
            "label": "高位浅突破幅度上限",
            "type": "float",
            "default": 0.01,
            "min": 0.001,
            "max": 0.05,
            "step": 0.001,
        },
        {
            "id": "exit_vol_ratio_min",
            "label": "放量阴线退出量比",
            "type": "float",
            "default": 1.5,
            "min": 1.0,
            "max": 5.0,
            "step": 0.1,
        },
    ],
    "scoring": {"momentum_20d": 0.6, "change_pct": 0.4},
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_volume_dry_breakout"]
EXIT_SIGNALS = ["signal_high_volume_bearish", "signal_ma20_breakdown"]
STOP_LOSS = -0.06
MAX_HOLD_DAYS = 20
ALERTS = []


class VolumeDryBreakoutMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"open", "high", "low", "close", "volume"})

    def required_warmup_bars(self, params: dict) -> int:
        del params
        return 60

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        previous_open = shift(market.open, 1)
        previous_high = shift(market.high, 1)
        previous_low = shift(market.low, 1)
        previous_close = shift(market.close, 1)
        previous_volume = shift(market.volume, 1)
        previous_vol_ratio = shift(matrix_feature(market, "vol_ratio_5d"), 1)

        setup_range = previous_high - previous_low
        setup_body = np.abs(previous_close - previous_open)
        setup_lower_wick = np.minimum(previous_open, previous_close) - previous_low
        entry = (
            (previous_vol_ratio >= float(params.get("setup_vol_ratio_min", 2.0)))
            & (setup_range > 0)
            & (
                setup_body
                <= setup_range * float(params.get("max_body_to_range", 0.35))
            )
            & (
                setup_lower_wick
                >= setup_range * float(params.get("min_lower_wick_to_range", 0.35))
            )
            & (market.close > previous_high)
            & (
                market.volume
                <= previous_volume * float(params.get("confirm_volume_ratio_max", 0.8))
            )
        )
        if params.get("require_bullish_confirm", True):
            entry &= market.close > market.open

        ma20 = matrix_feature(market, "ma20")
        if params.get("require_above_ma20", True):
            entry &= market.close > ma20
        if params.get("use_extension_filter", True):
            entry &= market.close <= ma20 * (1.0 + float(params.get("ma20_bias_max", 0.12)))
        if params.get("use_breakout_quality_guard", False):
            ma20_bias = market.close / ma20 - 1.0
            breakout_margin = market.close / previous_high - 1.0
            stretched_shallow_breakout = (
                ma20_bias
                >= float(params.get("breakout_guard_ma20_bias_min", 0.05))
            ) & (
                breakout_margin
                <= float(params.get("breakout_guard_margin_max", 0.01))
            )
            entry &= ~stretched_shallow_breakout

        current_vol_ratio = matrix_feature(market, "vol_ratio_5d")
        high_volume_bearish = (market.close < market.open) & (
            current_vol_ratio >= float(params.get("exit_vol_ratio_min", 1.5))
        )
        ma20_breakdown = (market.close < ma20) & (previous_close >= shift(ma20, 1))
        exit_ = high_volume_bearish | ma20_breakdown

        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(
                high_volume_bearish,
                0,
                np.where(ma20_breakdown, 1, -1),
            ).astype(np.int16),
            entry_signal_ids=("signal_volume_dry_breakout",),
            exit_signal_ids=("signal_high_volume_bearish", "signal_ma20_breakdown"),
        )


MATRIX_STRATEGY = VolumeDryBreakoutMatrixStrategy()
