import threading
import time

from app.desktop import _shutdown_uvicorn, _wait_for_server


def test_wait_for_server_stops_when_backend_thread_exits() -> None:
    ready_event = threading.Event()
    ready_event.set()

    started = time.monotonic()
    result = _wait_for_server(9, timeout=30, ready_event=ready_event)

    assert result is False
    assert time.monotonic() - started < 1


def test_shutdown_uvicorn_requests_exit_and_waits_for_thread() -> None:
    """关闭桌面窗口后应请求 Uvicorn 退出并等待后台线程。"""
    stopped = threading.Event()

    class FakeServer:
        """记录退出请求的最小 Uvicorn 测试替身。"""

        should_exit: bool = False

        def __setattr__(self, name: str, value: object) -> None:
            """在收到退出标志时唤醒模拟服务线程。"""
            object.__setattr__(self, name, value)
            if name == "should_exit" and value is True:
                stopped.set()

    server = FakeServer()
    server_thread = threading.Thread(target=stopped.wait)
    server_thread.start()

    _shutdown_uvicorn([server], server_thread, timeout=1)

    assert server.should_exit is True
    assert not server_thread.is_alive()
