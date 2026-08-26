"""A 股交易日历工具。"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from functools import lru_cache

import exchange_calendars as xcals

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _xshg_calendar():
    return xcals.get_calendar("XSHG")


def is_cn_trading_day(day: date) -> bool:
    """按上交所 XSHG 日历判断 A 股是否开市. 日历异常时安全地视为休市."""
    try:
        return bool(_xshg_calendar().is_session(day.isoformat()))
    except (TypeError, ValueError):
        logger.exception("XSHG trading calendar unavailable for %s", day)
        return False


@lru_cache(maxsize=4096)
def cn_trading_days_elapsed(acquired_date: date, current_date: date) -> int:
    """统计买入日之后至当前日期已完成的上交所交易日数量。"""
    if current_date <= acquired_date:
        return 0
    try:
        sessions = _xshg_calendar().sessions_in_range(
            (acquired_date + timedelta(days=1)).isoformat(),
            current_date.isoformat(),
        )
        return len(sessions)
    except (TypeError, ValueError):
        logger.exception(
            "XSHG trading-day distance unavailable for %s -> %s",
            acquired_date,
            current_date,
        )
        return 0
