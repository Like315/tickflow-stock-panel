"""龙头打板 v3 — 在 v2 基础上加入题材/板块效应识别。

v3 相对 v2 的优化（第四步迭代）：
  4. 板块效应过滤：该股所属题材（概念 gn / 行业 hy）当日涨停家数 >= min_theme_limits，
     只做"有合力"的主线板块，剔除孤军涨停（板块无反应的独涨板 = 庄股嫌疑）。
  5. 可选"板块内龙头"（theme_leader_only）：候选必须是自己所属最强板块内连板数最高的股票。

数据说明（防作弊披露）：
  - 板块映射来自 ext_data/ext_gn_ths（同花顺概念）与 ext_hy_ths（同花顺行业），
    mode=snapshot（当前快照，无历史时点）。行业一级归属一年内高度稳定，可用；
    概念有漂移风险（股票可能中途加入/退出概念），回测结果视为近似。
  - 板块效应统计使用 T 日收盘涨停家数，与 T+1 成交无重叠，无前视。

其余规则继承 v2：情绪双维度过滤、不板即走、空间龙收敛、-6% 止损、全成本模型。
"""

import numpy as np
import polars as pl

from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix, matrix_feature

# 数据路径由 __file__ 推导（backend/app/strategy/builtin/xxx.py → 项目根/data）
_DATA_ROOT = __file__.replace("\\", "/").rsplit("/", 4)[0] + "/../data"

_theme_maps: dict[str, dict] = {}


def _theme_map(source: str) -> dict[str, tuple[str, ...]]:
    """加载 symbol → 板块/概念 映射（模块级缓存）。"""
    if source in _theme_maps:
        return _theme_maps[source]
    if source == "hy":
        rel = "ext_data/ext_hy_ths/part.parquet"
        col = "所属同花顺行业"
        out: dict[str, tuple[str, ...]] = {}
        df = pl.read_parquet(_DATA_ROOT + "/" + rel)
        for row in df.iter_rows(named=True):
            raw = str(row.get(col) or "")
            first = raw.split("-")[0].strip()
            if first:
                out[str(row.get("symbol"))] = (first,)
    else:
        rel = "ext_data/ext_gn_ths/part.parquet"
        col = "所属概念"
        out = {}
        df = pl.read_parquet(_DATA_ROOT + "/" + rel)
        for row in df.iter_rows(named=True):
            items = tuple(x.strip() for x in str(row.get(col) or "").split(";") if x.strip())
            if items:
                out[str(row.get("symbol"))] = items
    _theme_maps[source] = out
    return out


def _theme_strength(
    market: MarketDataMatrix,
    theme_map: dict[str, tuple[str, ...]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """返回 (strength[n_t,n_a], best_theme_idx[n_a], theme_members[n_th], theme_index)。

    strength[t, a] = 该股所属板块在 t 日的涨停家数最大值（最强板块强度）。
    best_theme_idx[a] = 该股最强板块在 theme_index 中的下标。
    theme_members[i] = 板块 i 的成员 asset 索引数组。
    theme_index = 板块名列表（顺序与 theme_members 对应）。
    """
    symbols = market.symbols
    asset_themes = [theme_map.get(s, ()) for s in symbols]
    all_themes = sorted({t for ts in asset_themes for t in ts})
    t2i = {t: i for i, t in enumerate(all_themes)}
    n_th = len(all_themes)
    n_a = len(symbols)
    n_t = market.shape[0]

    rows, cols = [], []
    asset_tidx: list[list[int]] = []
    for ai, ts in enumerate(asset_themes):
        idxs = [t2i[t] for t in ts]
        asset_tidx.append(idxs)
        for ti in idxs:
            rows.append(ti)
            cols.append(ai)
    th_asset = np.zeros((n_th, n_a), dtype=np.float32)
    th_asset[rows, cols] = 1.0

    locked = market.limit_up_locked.astype(np.float32)
    theme_limits = locked @ th_asset.T  # [n_t, n_th] 板块每日涨停家数

    strength = np.zeros((n_t, n_a), dtype=np.float32)
    best_theme_idx = np.full(n_a, -1, dtype=np.int32)
    for ai, idxs in enumerate(asset_tidx):
        if not idxs:
            continue
        best = idxs[int(np.argmax(theme_limits[:, idxs].max(axis=0)))]
        best_theme_idx[ai] = best
        strength[:, ai] = theme_limits[:, best]

    theme_members = [np.flatnonzero(th_asset[i]).astype(np.int32) for i in range(n_th)]
    return strength, best_theme_idx, theme_members, all_themes


def _theme_leader_mask(
    market: MarketDataMatrix,
    boards: np.ndarray,
    best_theme_idx: np.ndarray,
    theme_members: list[np.ndarray],
    base_candidate: np.ndarray,
) -> np.ndarray:
    """候选必须是自己最强板块内连板数最高的股票之一（板块内空间龙）。"""
    n_t, n_a = market.shape
    leader = np.zeros((n_t, n_a), dtype=bool)
    cand_rows, cand_cols = np.nonzero(base_candidate)
    for ti, ai in zip(cand_rows, cand_cols):
        th = int(best_theme_idx[ai])
        if th < 0:
            continue
        members = theme_members[th]
        if members.size == 0:
            continue
        row = boards[ti, members]
        valid = row[np.isfinite(row)]
        if valid.size == 0:
            continue
        if boards[ti, ai] >= float(valid.max()) and boards[ti, ai] >= 1:
            leader[ti, ai] = True
    return leader


META = {
    "id": "limit_up_leader_v3",
    "name": "龙头打板v3",
    "description": "连板接力v3: 情绪双维度 + 题材板块效应(板块内涨停合力) + 板块内龙头 + 次日不板即走",
    "tags": ["涨停", "连板", "打板", "龙头", "情绪周期", "题材"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [
        {"id": "min_boards", "label": "最少连板数", "type": "int", "default": 2, "min": 1, "max": 6, "step": 1},
        {"id": "use_emotion_filter", "label": "启用情绪过滤", "type": "bool", "default": True},
        {"id": "min_daily_limit_ups", "label": "全市场涨停家数下限(广度)", "type": "int", "default": 80, "min": 5, "max": 200, "step": 5},
        {"id": "min_leader_height", "label": "当日最高连板下限(高度)", "type": "int", "default": 3, "min": 1, "max": 10, "step": 1},
        {"id": "min_amount_yi", "label": "最低成交额(亿)", "type": "float", "default": 3.0, "min": 0.5, "max": 30.0, "step": 0.5},
        {"id": "space_leader_only", "label": "只打当日最高板(空间龙)", "type": "bool", "default": False},
        {"id": "exit_on_not_locked", "label": "不板即走", "type": "bool", "default": True},
        {"id": "use_theme_filter", "label": "启用板块效应过滤", "type": "bool", "default": True},
        {"id": "theme_source", "label": "板块来源(gn概念/hy行业)", "type": "str", "default": "gn"},
        {"id": "min_theme_limits", "label": "板块内涨停家数下限", "type": "int", "default": 3, "min": 1, "max": 30, "step": 1},
        {"id": "theme_leader_only", "label": "仅板块内最高板", "type": "bool", "default": False},
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


class LimitUpLeaderV3MatrixStrategy:
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

        # 3) 情绪双维度（广度 + 高度）
        if params.get("use_emotion_filter", True):
            daily_limits = np.sum(market.limit_up_locked.astype(bool), axis=1)
            daily_max_height = np.max(np.where(np.isfinite(boards), boards, 0), axis=1)
            ok_day = (
                (daily_limits >= int(params.get("min_daily_limit_ups", 80)))
                & (daily_max_height >= int(params.get("min_leader_height", 3)))
            )
            entry &= ok_day[:, None]

        # 4) 板块效应过滤（v3 新增）
        strength = None
        best_theme_idx = None
        theme_members = None
        if params.get("use_theme_filter", True):
            theme_map = _theme_map(str(params.get("theme_source", "gn")))
            strength, best_theme_idx, theme_members, _ = _theme_strength(market, theme_map)
            entry &= strength >= float(params.get("min_theme_limits", 3))

        # 5) 空间龙收敛（v2 可选）
        if params.get("space_leader_only", False):
            daily_max_height = np.max(np.where(np.isfinite(boards), boards, 0), axis=1)
            entry &= boards >= daily_max_height[:, None]
            entry &= boards >= 1

        # 6) 板块内龙头（v3 可选）
        if params.get("theme_leader_only", False) and best_theme_idx is not None:
            base = entry.copy()
            leader = _theme_leader_mask(market, boards, best_theme_idx, theme_members, base)
            entry &= leader

        # 7) 卖出信号：不板即走
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


MATRIX_STRATEGY = LimitUpLeaderV3MatrixStrategy()
