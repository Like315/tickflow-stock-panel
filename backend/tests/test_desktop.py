import threading
import time

from app.desktop import _wait_for_server


def test_wait_for_server_stops_when_backend_thread_exits() -> None:
    ready_event = threading.Event()
    ready_event.set()

    started = time.monotonic()
    result = _wait_for_server(9, timeout=30, ready_event=ready_event)

    assert result is False
    assert time.monotonic() - started < 1
