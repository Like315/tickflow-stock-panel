from datetime import date

from app.market_calendar import is_cn_trading_day


def test_cn_trading_calendar_excludes_exchange_holidays_and_weekends() -> None:
    assert is_cn_trading_day(date(2026, 8, 20)) is True
    assert is_cn_trading_day(date(2026, 10, 1)) is False
    assert is_cn_trading_day(date(2026, 8, 22)) is False
