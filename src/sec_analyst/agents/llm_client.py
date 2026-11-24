"""LLM client protocol and OpenAI-compatible local inference adapter."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

import httpx


class LLMClient(Protocol):
    """Protocol for structured JSON and text chat completions."""

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def chat_text(self, system_prompt: str, user_prompt: str) -> str: ...


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Parse JSON even if wrapped in markdown code fences."""
    cleaned = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    return json.loads(cleaned)


class OpenAICompatibleLLMClient:
    """Calls a local llama.cpp / Ollama / vLLM OpenAI-compatible /v1/chat/completions API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8080/v1",
        model: str = "Qwen3-8B-Q4_K_M",
        api_key: str = "local",
        timeout: float = 120.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self._client = http_client or httpx.Client(timeout=timeout)

    def _post_chat(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if response_format is not None:
            payload["response_format"] = response_format

        resp = self._client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return str(data["choices"][0]["message"]["content"])

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fmt: dict[str, Any] = {"type": "json_object"}
        if json_schema is not None:
            fmt = {
                "type": "json_schema",
                "json_schema": {"name": "structured_output", "schema": json_schema},
            }
        raw = self._post_chat(system_prompt, user_prompt, response_format=fmt)
        return _extract_json_object(raw)

    def chat_text(self, system_prompt: str, user_prompt: str) -> str:
        return self._post_chat(system_prompt, user_prompt)
