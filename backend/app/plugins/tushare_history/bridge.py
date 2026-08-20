"""Small boundary around the optional Tushare SDK."""
from __future__ import annotations

import os
from datetime import datetime


class TushareHistoryError(RuntimeError):
    """Tushare dependency, credential, entitlement, or request failure."""


def availability() -> tuple[bool, str]:
    try:
        import tushare  # noqa: F401
    except ImportError:
        return False, "未安装 tushare Python 包"
    if not os.getenv("TUSHARE_TOKEN"):
        return False, "未设置 TUSHARE_TOKEN"
    return True, "ok (历史分钟权限将在首次请求时校验)"


def fetch_minutes(
    symbol: str,
    *,
    start_time: datetime,
    end_time: datetime,
    freq: str,
):
    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        raise TushareHistoryError("TUSHARE_TOKEN is not configured")
    try:
        import tushare as ts
    except ImportError as exc:
        raise TushareHistoryError("tushare Python package is not installed") from exc

    try:
        ts.set_token(token)
        return ts.pro_bar(
            ts_code=symbol,
            asset="E",
            adj=None,
            freq=freq,
            start_date=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            end_date=end_time.strftime("%Y-%m-%d %H:%M:%S"),
        )
    except Exception as exc:
        raise TushareHistoryError(f"Tushare historical minute request failed: {exc}") from exc
