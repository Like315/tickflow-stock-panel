"""A 股交易日历工具。"""

from __future__ import annotations

import logging
from datetime import date
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
