"""Offline FastAPI contract tests for /health, /analyze (SSE), and /ingest."""

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from sec_analyst.api.app import create_app
from sec_analyst.config import Settings
from sec_analyst.ingestion.edgar_client import EdgarClient
from tests.test_ingestion_pipeline import FakeEmbedder, InMemoryStore, _mock_edgar_transport
from tests.test_langgraph_pipeline import (
    FakeRetriever,
    FakeSqlExecutor,
    ScriptedLLMClient,
    _nvda_plan,
    _valid_citation,
)


def test_health_and_analyze_sse_stream() -> None:
    llm = ScriptedLLMClient(
        [
            _nvda_plan(),
            {
                "answer": "NVIDIA FY2024 revenue was $60.92B (+125.85% YoY).",
                "citations": [_valid_citation()],
            },
        ]
    )
    app = create_app(
        settings=Settings(llm_model="Qwen3-8B-Q4_K_M"),
        llm_client=llm,
        sql_executor=FakeSqlExecutor(),
        retriever=FakeRetriever(),
    )
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["model"] == "Qwen3-8B-Q4_K_M"

    resp = client.post(
        "/analyze",
        json={
            "question": "What was NVDA FY2024 revenue growth?",
            "indexed_tickers": ["NVDA"],
            "indexed_years": [2023, 2024],
        },
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    body = resp.text
    assert "event: node_update" in body
    assert "event: final" in body
    assert "+125.85%" in body


def test_ingest_endpoint_on_demand(tmp_path: Path) -> None:
    edgar = EdgarClient(
        user_agent="AutonomousSECAnalyst/0.1 test@example.com",
        cache_dir=tmp_path,
        max_rps=10.0,
        http_client=httpx.Client(transport=_mock_edgar_transport()),
    )
    app = create_app(
        ingestion_store=InMemoryStore(),
        embedder=FakeEmbedder(),
        edgar_client=edgar,
    )
    client = TestClient(app)

    resp = client.post("/ingest", json={"ticker": "NVDA", "fiscal_years": [2024]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticker"] == "NVDA"
    assert data["filings_processed"] == 1
    assert data["xbrl_facts_written"] == 1
