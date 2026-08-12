# ruff: noqa: RUF001
from __future__ import annotations

from datetime import date

import httpx

from app.services.research_agent_announcements import CninfoAnnouncementProvider, _safe_cninfo_url


def test_safe_url_allows_only_cninfo_https() -> None:
    assert _safe_cninfo_url("finalpage/2026-08-08/1.PDF") == "https://static.cninfo.com.cn/finalpage/2026-08-08/1.PDF"
    assert _safe_cninfo_url("https://evil.example/x.pdf") is None
    assert _safe_cninfo_url("http://static.cninfo.com.cn/x.pdf") is None


def test_provider_parses_metadata_and_caches(monkeypatch) -> None:
    calls = 0

    def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"announcements": [{
            "secCode": "000001",
            "announcementTitle": "<em>平安银行</em>：业绩说明会公告",
            "announcementTime": 1786118400000,
            "adjunctUrl": "finalpage/2026-08-08/1225463626.PDF",
        }]}, request=httpx.Request("GET", "https://www.cninfo.com.cn"))

    monkeypatch.setattr(httpx, "get", fake_get)
    provider = CninfoAnnouncementProvider()
    first = provider.fetch("000001.SZ", end_date=date(2026, 8, 12))
    second = provider.fetch("000001.SZ", end_date=date(2026, 8, 12))
    assert first["available"] is True
    assert first["announcements"][0]["title"] == "平安银行：业绩说明会公告"
    assert first["announcements"][0]["url"].startswith("https://static.cninfo.com.cn/")
    assert second == first
    assert calls == 1


def test_provider_fails_closed(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(httpx, "get", fail)
    result = CninfoAnnouncementProvider().fetch("600000.SH", end_date=date(2026, 8, 12))
    assert result["available"] is False
    assert result["announcements"] == []
    assert "暂不可用" in result["message"]


def test_provider_rejects_future_announcement(monkeypatch) -> None:
    def fake_get(*args, **kwargs):
        return httpx.Response(200, json={"announcements": [{
            "secCode": "000001",
            "announcementTitle": "未来公告",
            "announcementTime": 1786492800000,
            "adjunctUrl": "finalpage/2026-08-12/future.PDF",
        }]}, request=httpx.Request("GET", "https://www.cninfo.com.cn"))

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CninfoAnnouncementProvider().fetch(
        "000001.SZ", end_date=date(2026, 8, 10)
    )
    assert result["announcements"] == []
