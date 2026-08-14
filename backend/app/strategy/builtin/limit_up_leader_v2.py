"""龙头打板 v2 — 在 v1 基础上优化三个环节。

v2 相对 v1 的优化：
  1. 情绪过滤双维度：涨停家数（广度）+ 当日最高连板高度（赚钱效应高度），
     只有"广度 + 高度"同时满足才出手 —— 退潮/断层期空仓。
  2. 卖出改"不板即走"：持仓日收盘未封板 → 次日开盘卖出；涨停则继续持有
     （让利润奔跑）。由策略级 exit 信号实现，引擎侧保留 -6% 止损兜底。
  3. 可选"空间龙专注"：space_leader_only=True 时只打当日最高连板梯队的股票，
     进一步收敛到市场空间板。

防作弊口径与 v1 一致：信号只用 T 日收盘数据；T+1 开盘成交；
一字板/跌停/停牌由引擎在成交日拦截；全成本模型。
"""

import numpy as np

from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix, matrix_feature

META = {
    "id": "limit_up_leader_v2",
    "name": "龙头打板v2",
    "description": "连板接力v2: 情绪双维度(涨停家数+连板高度)过滤 + 空间龙头评分 + 次日不板即走",
    "tags": ["涨停", "连板", "打板", "龙头", "情绪周期"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [
        {"id": "min_boards", "label": "最少连板数", "type": "int", "default": 2, "min": 1, "max": 6, "step": 1},
        {"id": "use_emotion_filter", "label": "启用情绪过滤", "type": "bool", "default": True},
        {
            "id": "min_daily_limit_ups",
            "label": "全市场涨停家数下限(广度)",
            "type": "int",
            "default": 80,
            "min": 5,
            "max": 200,
            "step": 5,
        },
        {
            "id": "min_leader_height",
            "label": "当日最高连板下限(高度)",
            "type": "int",
            "default": 3,
            "min": 1,
            "max": 10,
            "step": 1,
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
        {"id": "space_leader_only", "label": "只打当日最高板(空间龙)", "type": "bool", "default": False},
        {"id": "exit_on_not_locked", "label": "不板即走(收盘未封板次日卖)", "type": "bool", "default": True},
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
MAX_HOLD_DAYS = 6  # 兜底上限：连续涨停可一直持有，但不允许超过 6 个交易日
ALERTS = []


class LimitUpLeaderV2MatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close", "consecutive_limit_ups", "amount", "turnover_rate"})

    def required_warmup_bars(self, params: dict) -> int:
        del params
        return 60

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        shape = market.shape
        entry = market.limit_up_locked.astype(bool).copy()

        # 1) 连板数过滤
        boards = matrix_feature(market, "consecutive_limit_ups")
        entry &= boards >= int(params.get("min_boards", 2))

        # 2) 成交额过滤
        min_amount = float(params.get("min_amount_yi", 3.0)) * 1e8
        if min_amount > 0:
            amount = market.field("amount")
            valid = np.isfinite(amount)
            entry &= np.where(valid, amount >= min_amount, False)

        # 3) 情绪双维度（广度 + 高度），全部用 T 日数据，无前视
        if params.get("use_emotion_filter", True):
            daily_limits = np.sum(market.limit_up_locked.astype(bool), axis=1)  # 广度
            daily_max_height = np.max(
                np.where(np.isfinite(boards), boards, 0), axis=1
            )  # 高度
            min_daily = int(params.get("min_daily_limit_ups", 80))
            min_height = int(params.get("min_leader_height", 3))
            ok_day = (daily_limits >= min_daily) & (daily_max_height >= min_height)
            entry &= ok_day[:, None]

        # 4) 可选：只打当日最高板（空间龙专注）
        if params.get("space_leader_only", False):
            daily_max_height = np.max(np.where(np.isfinite(boards), boards, 0), axis=1)
            entry &= boards >= daily_max_height[:, None]
            # 至少保证是当天最强的票（最高板可能只有 1 板）
            entry &= boards >= 1

        # 5) 卖出信号：持仓日收盘未封板 → 次日开盘卖出（"不板即走"）
        exit_sig = None
        if params.get("exit_on_not_locked", True):
            exit_sig = (~market.limit_up_locked.astype(bool)).astype(np.uint8)

        return make_signal_matrix(
            shape,
            entry=entry.astype(np.uint8),
            exit=exit_sig,
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_sig, 0, -1).astype(np.int16) if exit_sig is not None else None,
            entry_signal_ids=("signal_limit_up",),
            exit_signal_ids=("not_locked",),
        )


MATRIX_STRATEGY = LimitUpLeaderV2MatrixStrategy()
