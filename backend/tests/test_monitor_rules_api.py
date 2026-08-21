from app.api.monitor_rules import _context_rule_warning


def test_display_only_context_does_not_report_paused_entry() -> None:
    """仅展示模式在来源不可用时不能误报已暂停入场。"""
    rule = {
        "context_filters": {
            "overnight_us": {"mode": "display_only"},
            "news": {"mode": "off"},
            "unavailable_action": "pause",
        }
    }
    status = {"overnight_us": {"available": False}}

    warning = _context_rule_warning(rule, status)

    assert "仅展示已跳过" in warning
    assert "已暂停入场" not in warning


def test_blocking_context_reports_paused_entry() -> None:
    """阻断模式与暂停策略组合时应明确报告入场暂停。"""
    rule = {
        "context_filters": {
            "overnight_us": {"mode": "risk_gate"},
            "news": {"mode": "off"},
            "unavailable_action": "pause",
        }
    }
    status = {"overnight_us": {"available": False}}

    assert "已暂停入场" in _context_rule_warning(rule, status)


def test_news_no_data_is_a_valid_neutral_context() -> None:
    """新闻源明确无相关新闻时不应作为数据不可用告警。"""
    rule = {
        "context_filters": {
            "overnight_us": {"mode": "off"},
            "news": {"mode": "negative_veto"},
            "unavailable_action": "pause",
        }
    }
    status = {"news": {"available": False, "status": "no_data"}}

    assert _context_rule_warning(rule, status) == ""
