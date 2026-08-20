from __future__ import annotations

import time
from types import SimpleNamespace

from app.api import alerts
from app.services import alert_store


def test_list_recent_filters_symbols_before_limit(tmp_path) -> None:
    now_ms = int(time.time() * 1000)
    alert_store.append_many(
        tmp_path,
        [
            {"ts": now_ms, "symbol": "600519.SH", "source": "strategy", "type": "buy_signal"},
            {"ts": now_ms - 1, "symbol": "000001.SZ", "source": "strategy", "type": "sell_signal"},
            {"ts": now_ms - 2, "symbol": "000001.SZ", "source": "strategy", "type": "buy_signal"},
        ],
    )

    result = alert_store.list_recent(tmp_path, limit=1, symbols={"000001.SZ"})

    assert [event["ts"] for event in result] == [now_ms - 1]


def test_list_recent_with_empty_symbol_set_returns_no_events(tmp_path) -> None:
    alert_store.append(
        tmp_path,
        {"ts": int(time.time() * 1000), "symbol": "600519.SH", "source": "strategy"},
    )

    assert alert_store.list_recent(tmp_path, symbols=set()) == []


def test_alerts_api_parses_symbol_filter(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_list_recent(data_dir, **kwargs):
        captured["data_dir"] = data_dir
        captured.update(kwargs)
        return []

    monkeypatch.setattr(alerts.alert_store, "list_recent", fake_list_recent)
    monkeypatch.setattr(alerts.alert_store, "count", lambda _data_dir: 0)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))),
        ),
    )

    result = alerts.list_alerts(request, symbols="600519.SH, 000001.SZ,600519.SH")

    assert captured["symbols"] == {"600519.SH", "000001.SZ"}
    assert result == {"alerts": [], "total": 0}


def test_alerts_api_keeps_empty_symbol_filter(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_list_recent(_data_dir, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(alerts.alert_store, "list_recent", fake_list_recent)
    monkeypatch.setattr(alerts.alert_store, "count", lambda _data_dir: 0)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))),
        ),
    )

    alerts.list_alerts(request, symbols="")

    assert captured["symbols"] == set()
