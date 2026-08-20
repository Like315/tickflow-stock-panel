"""Three staged research versions of the volume-dry-breakout strategy."""

from __future__ import annotations

from dataclasses import dataclass

STRICT_PARAMS = {
    "setup_vol_ratio_min": 2.5,
    "max_body_to_range": 0.25,
    "min_lower_wick_to_range": 0.45,
    "confirm_volume_ratio_max": 0.70,
    "ma20_bias_max": 0.08,
}

C_QUALITY_PARAMS = {
    **STRICT_PARAMS,
    "use_breakout_quality_guard": True,
    "breakout_guard_ma20_bias_min": 0.05,
    "breakout_guard_margin_max": 0.01,
}

SECTOR_LEADER_SCORE = {
    "enabled": True,
    "kind": "industry",
    "level": 1,
    # Daily close T context; orders are executed at T+1 open.
    "lag_bars": 0,
    "apply_as": "score",
    "score_weight": 0.80,
    "trend_window": 10,
    "trend_daily_top": 10,
    "trend_top": 8,
    "min_trend_return": 0.02,
    "min_trend_up_ratio": 0.55,
    "min_trend_top_ratio": 0.30,
    "mainline_window": 5,
    "mainline_top": 5,
    "min_mainline_limit_ups": 3,
    "min_mainline_active_days": 2,
    "min_mainline_limit_share": 0.02,
    "min_mainline_height": 1,
    "leader_window": 20,
    "min_coverage": 0.50,
}


@dataclass(frozen=True)
class VolumeDryBreakoutVersion:
    id: str
    label: str
    params: dict
    overrides: dict
    max_exposure_pct: float
    max_positions: int


VERSIONS = (
    VolumeDryBreakoutVersion(
        id="strict_price_volume",
        label="A-严格量价",
        params=dict(STRICT_PARAMS),
        overrides={},
        max_exposure_pct=1.0,
        max_positions=10,
    ),
    VolumeDryBreakoutVersion(
        id="sector_leader_score",
        label="B-板块龙头评分",
        params=dict(STRICT_PARAMS),
        overrides={"sector_context_filter": dict(SECTOR_LEADER_SCORE)},
        max_exposure_pct=1.0,
        max_positions=10,
    ),
    VolumeDryBreakoutVersion(
        id="regime_risk_control",
        label="C-市场阶段风控",
        params=dict(C_QUALITY_PARAMS),
        overrides={
            "sector_context_filter": {
                **SECTOR_LEADER_SCORE,
                "market_min_score": 50.0,
                "market_min_consecutive_days": 2,
            }
        },
        max_exposure_pct=0.50,
        max_positions=3,
    ),
)
