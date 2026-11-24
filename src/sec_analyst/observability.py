"""Lightweight observability spans with optional Langfuse integration."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from pydantic import BaseModel, Field


class SpanRecord(BaseModel):
    """Single execution span for a LangGraph node or tool call."""

    name: str
    duration_ms: float
    attributes: dict[str, Any] = Field(default_factory=dict)


class TraceRecorder:
    """Collects per-node latency & metadata spans; optionally mirrors to Langfuse."""

    def __init__(self, trace_name: str = "sec-analyst-run") -> None:
        self.trace_name = trace_name
        self.spans: list[SpanRecord] = []
        self._langfuse_trace = self._init_langfuse(trace_name)

    @staticmethod
    def _init_langfuse(trace_name: str):
        if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
            return None
        try:
            from langfuse import Langfuse

            client = Langfuse()
            return client.trace(name=trace_name)
        except (ImportError, RuntimeError, OSError):
            return None

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[dict[str, Any]]:
        """Measure elapsed time for `name` and record structured attributes."""
        start = time.perf_counter()
        attrs = dict(attributes)
        try:
            yield attrs
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)
            record = SpanRecord(name=name, duration_ms=elapsed_ms, attributes=attrs)
            self.spans.append(record)
            if self._langfuse_trace is not None:
                self._langfuse_trace.span(name=name, metadata={**attrs, "duration_ms": elapsed_ms})
