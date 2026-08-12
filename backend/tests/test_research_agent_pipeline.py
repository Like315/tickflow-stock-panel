from __future__ import annotations

from types import SimpleNamespace

from app.jobs.daily_pipeline import _submit_research_agent_cycle


class FakeService:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def submit_daily_cycle(self, trigger: str):
        self.calls += 1
        if self.fail:
            raise RuntimeError("AI unavailable")
        return {"status": "started", "trigger": trigger}


def test_pipeline_submits_automatic_cycle() -> None:
    service = FakeService()
    result = _submit_research_agent_cycle(SimpleNamespace(research_agent_service=service))
    assert result == {"status": "started", "trigger": "automatic"}
    assert service.calls == 1


def test_agent_submit_failure_does_not_escape_pipeline() -> None:
    service = FakeService(fail=True)
    assert _submit_research_agent_cycle(SimpleNamespace(research_agent_service=service)) is None
    assert service.calls == 1


def test_pipeline_without_agent_is_noop() -> None:
    assert _submit_research_agent_cycle(SimpleNamespace()) is None
