"""Offline tests for Phase 4 UI serving and observability spans."""

from fastapi.testclient import TestClient

from sec_analyst.api.app import create_app
from sec_analyst.observability import TraceRecorder


def test_ui_and_static_assets_served() -> None:
    app = create_app()
    client = TestClient(app)

    home = client.get("/")
    assert home.status_code == 200
    assert "SEC Financial Analyst" in home.text
    assert "/static/analyst.js" in home.text

    js = client.get("/static/analyst.js")
    assert js.status_code == 200
    assert "/analyze" in js.text


def test_trace_recorder_captures_spans() -> None:
    recorder = TraceRecorder(trace_name="test-run")
    with recorder.span("planner", ticker="NVDA") as attrs:
        attrs["subtasks"] = 2

    assert len(recorder.spans) == 1
    assert recorder.spans[0].name == "planner"
    assert recorder.spans[0].duration_ms >= 0.0
    assert recorder.spans[0].attributes["subtasks"] == 2
