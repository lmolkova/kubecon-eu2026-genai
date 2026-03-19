"""Tempo HTTP client for querying traces."""

import os

import httpx

TEMPO_URL = os.environ.get("TEMPO_URL", "http://localhost:3200")


class TempoService:
    def __init__(self) -> None:
        self._client = httpx.Client()

    def search(self, start_ns: int, end_ns: int) -> list[dict]:
        """Return traces with an invoke_workflow span in the given time window."""
        q = '{resource.service.name="hallucHR" && span.gen_ai.operation.name="invoke_workflow"}'
        resp = self._client.get(
            f"{TEMPO_URL}/api/search",
            params={"q": q, "start": start_ns // 1_000_000_000, "end": end_ns // 1_000_000_000, "limit": 20},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("traces", [])

    def get_trace(self, trace_id: str) -> dict:
        resp = self._client.get(f"{TEMPO_URL}/api/traces/{trace_id}", timeout=10)
        resp.raise_for_status()
        return resp.json()
