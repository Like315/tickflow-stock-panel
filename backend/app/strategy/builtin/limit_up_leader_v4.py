"""龙头打板 v4 — 在 v3 基础上加入两个动态情绪指标。

v4 相对 v3 的优化（第六步迭代）：
  6. 溢价温度计：昨日涨停池（T-1 收盘封板）今日平均涨幅 >= min_premium_pct 才出手。
     直接度量"昨天打板的人今天赚不赚钱"——接力亏钱效应明显时（平均溢价为负），
     打板就是送钱，空仓等待。这是打板体系最核心的赚钱效应指标。
  7. 行业涨跌强度：所属一级行业当日全部成分股平均涨幅 >= min_industry_change_pct。
     替代/叠加静态"行业涨停家数"，反映行业整体资金进攻强度（涨停家数多但普跌
     的行业是"高位派发"，不应参与）。

防作弊：溢价与行业强度均用 T 日收盘数据计算，与 T+1 成交无重叠，无未来函数。

其余规则继承 v3：情绪双维度、行业板块效应、空间龙、不板即走、-6% 止损、全成本模型。
"""

import numpy as np
import polars as pl

from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix, matrix_feature

_DATA_ROOT = __file__.replace("\\", "/").rsplit("/", 4)[0] + "/../data"

_theme_maps: dict[str, dict] = {}


def _hy_map() -> dict[str, tuple[str, ...]]:
    if "hy" in _theme_maps:
        return _theme_maps["hy"]
    out: dict[str, tuple[str, ...]] = {}
    df = pl.read_parquet(_DATA_ROOT + "/ext_data/ext_hy_ths/part.parquet")
    for row in df.iter_rows(named=True):
        raw = str(row.get("所属同花顺行业") or "")
        first = raw.split("-")[0].strip()
        if first:
            out[str(row.get("symbol"))] = (first,)
    _theme_maps["hy"] = out
    return out


def _industry_daily_change(market: MarketDataMatrix, hy_map: dict) -> tuple[np.ndarray, np.ndarray]:
    """返回 (industry_change[n_t, n_a], industry_limits[n_t, n_a])。

    industry_change[t, a] = 该股所属一级行业 t 日全部成分股平均涨幅。
    industry_limits[t, a] = 该股所属一级行业 t 日涨停家数（v3 指标保留）。
    """
    symbols = market.symbols
    industries = sorted({v[0] for v in hy_map.values() if v})
    i2i = {ind: i for i, ind in enumerate(industries)}
    n_i = len(industries)
    n_a = len(symbols)
    n_t = market.shape[0]

    rows, cols = [], []
    asset_ind: list[int] = []
    for ai, s in enumerate(symbols):
        inds = hy_map.get(s, ())
        if not inds or inds[0] not in i2i:
            asset_ind.append(-1)
            continue
        ii = i2i[inds[0]]
        asset_ind.append(ii)
        rows.append(ii)
        cols.append(ai)
    ind_asset = np.zeros((n_i, n_a), dtype=np.float32)
    ind_asset[rows, cols] = 1.0
    counts = ind_asset.sum(axis=1).reshape(1, -1)  # [1, n_i] 每行业成分股数

    change = matrix_feature(market, "change_pct")
    change_valid = np.where(np.isfinite(change), change, 0.0)
    ind_change_sum = change_valid @ ind_asset.T  # [n_t, n_i]
    ind_change = np.where(counts > 0, ind_change_sum / np.maximum(counts, 1), np.nan)

    locked = market.limit_up_locked.astype(np.float32)
    ind_limits = locked @ ind_asset.T  # [n_t, n_i]

    strength = np.full((n_t, n_a), np.nan, dtype=np.float32)
    limits = np.zeros((n_t, n_a), dtype=np.float32)
    for ai, ii in enumerate(asset_ind):
        if ii >= 0:
            strength[:, ai] = ind_change[:, ii]
            limits[:, ai] = ind_limits[:, ii]
    return strength, limits


def _prev_premium(market: MarketDataMatrix) -> np.ndarray:
    """昨日涨停池今日平均涨幅（溢价温度计）。"""
    locked = market.limit_up_locked.astype(bool)
    change = matrix_feature(market, "change_pct")
    n_t, n_a = locked.shape
    premium = np.zeros(n_t, dtype=np.float32)
    for t in range(1, n_t):
        pool = locked[t - 1]
        if not pool.any():
            continue
        vals = change[t, pool]
        vals = vals[np.isfinite(vals)]
        if vals.size:
            premium[t] = float(vals.mean())
    return premium


META = {
    "id": "limit_up_leader_v4",
    "name": "龙头打板v4",
    "description": "连板接力v4: 情绪双维度 + 行业板块效应 + 溢价温度计(昨日涨停今日表现) + 行业涨跌强度 + 空间龙 + 不板即走",
    "tags": ["涨停", "连板", "打板", "龙头", "情绪周期", "题材"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [
        {"id": "min_boards", "label": "最少连板数", "type": "int", "default": 2, "min": 1, "max": 6, "step": 1},
        {"id": "max_boards", "label": "最多连板数(0=不限)", "type": "int", "default": 0, "min": 0, "max": 12, "step": 1},
        {"id": "use_emotion_filter", "label": "启用情绪过滤", "type": "bool", "default": True},
        {"id": "min_daily_limit_ups", "label": "全市场涨停家数下限(广度)", "type": "int", "default": 80, "min": 5, "max": 200, "step": 5},
        {"id": "min_leader_height", "label": "当日最高连板下限(高度)", "type": "int", "default": 3, "min": 1, "max": 10, "step": 1},
        {"id": "min_amount_yi", "label": "最低成交额(亿)", "type": "float", "default": 3.0, "min": 0.5, "max": 30.0, "step": 0.5},
        {"id": "space_leader_only", "label": "只打当日最高板(空间龙)", "type": "bool", "default": True},
        {"id": "exit_on_not_locked", "label": "不板即走", "type": "bool", "default": True},
        {"id": "use_theme_filter", "label": "启用行业板块效应过滤", "type": "bool", "default": True},
        {"id": "min_theme_limits", "label": "行业涨停家数下限", "type": "int", "default": 5, "min": 1, "max": 30, "step": 1},
        {"id": "use_premium_filter", "label": "启用溢价温度计", "type": "bool", "default": True},
        {"id": "min_premium_pct", "label": "昨日涨停池今日平均涨幅下限%", "type": "float", "default": -2.0, "min": -10.0, "max": 5.0, "step": 0.5},
        {"id": "use_industry_strength", "label": "启用行业涨跌强度过滤", "type": "bool", "default": True},
        {"id": "min_industry_change_pct", "label": "行业平均涨幅下限%", "type": "float", "default": 1.0, "min": -3.0, "max": 8.0, "step": 0.5},
        {"id": "skip_long_gap", "label": "规避长假后首日跳空(买入信号前有>4天间隔则跳过)", "type": "bool", "default": True},
        {"id": "min_turnover_pct", "label": "最低换手率%(0=不限)", "type": "float", "default": 0.0, "min": 0.0, "max": 30.0, "step": 0.5},
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
MAX_HOLD_DAYS = 6
ALERTS = []


class LimitUpLeaderV4MatrixStrategy:
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
        max_boards = int(params.get("max_boards", 0))
        if max_boards > 0:
            entry &= boards <= max_boards

        # 2) 成交额过滤
        min_amount = float(params.get("min_amount_yi", 3.0)) * 1e8
        if min_amount > 0:
            amount = market.field("amount")
            valid = np.isfinite(amount)
            entry &= np.where(valid, amount >= min_amount, False)

        # 2.5) 换手率过滤（换手充分的板质量更高）
        min_turnover = float(params.get("min_turnover_pct", 0.0))
        if min_turnover > 0 and "turnover_rate" in market.fields:
            tr = market.field("turnover_rate")
            tr_valid = np.isfinite(tr)
            entry &= np.where(tr_valid, tr * 100.0 >= min_turnover, False)

        # 3) 情绪双维度（广度 + 高度）
        if params.get("use_emotion_filter", True):
            daily_limits = np.sum(market.limit_up_locked.astype(bool), axis=1)
            daily_max_height = np.max(np.where(np.isfinite(boards), boards, 0), axis=1)
            ok_day = (
                (daily_limits >= int(params.get("min_daily_limit_ups", 80)))
                & (daily_max_height >= int(params.get("min_leader_height", 3)))
            )
            entry &= ok_day[:, None]

        # 4) 溢价温度计（v4 新增）：昨日涨停池今日平均涨幅
        if params.get("use_premium_filter", True):
            premium = _prev_premium(market)
            min_prem = float(params.get("min_premium_pct", -2.0)) / 100.0
            entry &= premium[:, None] >= min_prem

        # 5) 行业板块效应 + 行业涨跌强度（v3 静态家数 + v4 动态强度）
        if params.get("use_theme_filter", True) or params.get("use_industry_strength", True):
            hy_map = _hy_map()
            ind_change, ind_limits = _industry_daily_change(market, hy_map)
            if params.get("use_theme_filter", True):
                entry &= ind_limits >= float(params.get("min_theme_limits", 5))
            if params.get("use_industry_strength", True):
                min_ichg = float(params.get("min_industry_change_pct", 1.0)) / 100.0
                entry &= np.where(np.isfinite(ind_change), ind_change >= min_ichg, False)

        # 6) 空间龙收敛
        if params.get("space_leader_only", False):
            daily_max_height = np.max(np.where(np.isfinite(boards), boards, 0), axis=1)
            entry &= boards >= daily_max_height[:, None]
            entry &= boards >= 1

        # 6.5) 长假风险规避：T+1（成交日）与 T（信号日）间隔 >4 天（跨长假）则跳过
        #      —— 节后首日跳空无法控制，最好的规避是节前不接力
        if params.get("skip_long_gap", True):
            try:
                ts = np.asarray(market.timestamps)
                if ts.size > 1 and np.issubdtype(ts.dtype, np.datetime64):
                    days = ts.astype("datetime64[D]").astype(np.int64)
                    gaps = np.diff(days)
                    skip_mask = np.zeros(shape[0], dtype=bool)
                    skip_mask[:-1] = gaps > 4
                    entry &= (~skip_mask)[:, None]
            except Exception:
                pass

        # 7) 卖出：不板即走
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


MATRIX_STRATEGY = LimitUpLeaderV4MatrixStrategy()
