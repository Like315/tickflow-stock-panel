"""龙头打板 — 连板接力打空间龙头。

规则（全部只用 T 日收盘后可得数据，T+1 开盘成交，杜绝未来函数）：
  1. T 日收盘封板（signal_limit_up / limit_up_locked）
  2. 连板数 >= min_boards（默认 2，二板及以上）
  3. 成交额 >= min_amount_yi 亿（流动性过滤，剔除小盘封单薄弱的票）
  4. 情绪过滤：T 日全市场收盘涨停家数 >= min_daily_limit_ups（退潮期空仓）
  5. 评分排序（连板数 50% + 成交额 30% + 换手率 20%），组合撮合按分数从高到低选股，
     资金优先流向空间龙头/中军 —— 即"只打最强的板"

卖出侧由回测配置承担：STOP_LOSS=-6% + MAX_HOLD_DAYS=2（次日不板就走，最多拿 2 个交易日），
对应打板"隔日溢价兑现 + 不恋战"的纪律。一字板买不进由引擎在 T+1 成交日拦截。
"""

import numpy as np

from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix, matrix_feature

META = {
    "id": "limit_up_leader",
    "name": "龙头打板",
    "description": "连板接力: 打当日涨停且连板数达标的空间龙头, 情绪过滤 + 成交额/换手评分排序, T+1 开盘买入",
    "tags": ["涨停", "连板", "打板", "龙头"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [
        {"id": "min_boards", "label": "最少连板数", "type": "int", "default": 2, "min": 1, "max": 6, "step": 1},
        {"id": "use_emotion_filter", "label": "启用情绪过滤", "type": "bool", "default": True},
        {
            "id": "min_daily_limit_ups",
            "label": "全市场涨停家数下限",
            "type": "int",
            "default": 30,
            "min": 5,
            "max": 200,
            "step": 5,
        },
        {
            "id": "min_amount_yi",
            "label": "最低成交额(亿)",
            "type": "float",
            "default": 3.0,
            "min": 0.5,
            "max": 30.0,
            "step": 0.5,
        },
    ],
    "scoring": {"consecutive_limit_ups": 0.5, "amount": 0.3, "turnover_rate": 0.2},
    "order_by": "score",
    "descending": True,
    "limit": 50,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_limit_up"]
EXIT_SIGNALS = []
STOP_LOSS = -0.06
MAX_HOLD_DAYS = 2
ALERTS = []


class LimitUpLeaderMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close", "consecutive_limit_ups", "amount", "turnover_rate"})

    def required_warmup_bars(self, params: dict) -> int:
        del params
        return 60

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        # 1) T 日收盘封板
        entry = market.limit_up_locked.astype(bool).copy()

        # 2) 连板数过滤（consecutive_limit_ups 为纯历史递推，无前视）
        boards = matrix_feature(market, "consecutive_limit_ups")
        min_boards = int(params.get("min_boards", 2))
        entry &= boards >= min_boards

        # 3) 成交额过滤（元 -> 亿），NaN 视为不满足
        min_amount = float(params.get("min_amount_yi", 3.0)) * 1e8
        if min_amount > 0:
            amount = market.field("amount")
            valid = np.isfinite(amount)
            entry &= np.where(valid, amount >= min_amount, False)

        # 4) 情绪过滤：T 日全市场收盘涨停家数（limit_up_locked 逐日求和），
        #    家数过少说明退潮/冰点，空仓等待。
        if params.get("use_emotion_filter", True):
            min_daily = int(params.get("min_daily_limit_ups", 30))
            daily_limits = np.sum(market.limit_up_locked.astype(bool), axis=1)
            entry &= daily_limits[:, None] >= min_daily

        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            entry_signal_ids=("signal_limit_up",),
        )


MATRIX_STRATEGY = LimitUpLeaderMatrixStrategy()
