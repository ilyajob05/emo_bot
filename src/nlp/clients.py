"""
Async HTTP clients for external NLP services (embeddings + emotion classification).

Includes circuit breaker for production reliability — if the service is down,
calls fail fast and fall back to deterministic methods.
"""

from __future__ import annotations

import math
import time
from typing import Any

import httpx

from .config import (
    NLP_SERVICE_URL,
    NLP_REQUEST_TIMEOUT,
    NLP_MAX_RETRIES,
    NLP_CIRCUIT_BREAKER_THRESHOLD,
    NLP_CIRCUIT_BREAKER_RESET,
)


# ─── Circuit Breaker ─────────────────────────────────────────────────────────

class CircuitBreaker:
    """Simple circuit breaker: closed → open (after N failures) → half-open (after timeout)."""

    def __init__(self, threshold: int, reset_seconds: float):
        self._threshold = threshold
        self._reset_seconds = reset_seconds
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._state = "closed"  # closed | open | half-open

    @property
    def is_open(self) -> bool:
        if self._state == "open":
            if time.monotonic() - self._last_failure_time >= self._reset_seconds:
                self._state = "half-open"
                return False
            return True
        return False

    def record_success(self) -> None:
        self._failure_count = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self._threshold:
            self._state = "open"


# ─── Cosine Similarity ───────────────────────────────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors (pure Python)."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ─── NLP Service Client ──────────────────────────────────────────────────────

class NlpServiceClient:
    """Async client for the external NLP service (embeddings + emotion).

    All methods return None when the service is unavailable,
    allowing callers to fall back to deterministic methods.
    """

    def __init__(
        self,
        base_url: str = NLP_SERVICE_URL,
        timeout: float = NLP_REQUEST_TIMEOUT,
        max_retries: int = NLP_MAX_RETRIES,
        circuit_threshold: int = NLP_CIRCUIT_BREAKER_THRESHOLD,
        circuit_reset: float = NLP_CIRCUIT_BREAKER_RESET,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._circuit = CircuitBreaker(circuit_threshold, circuit_reset)
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """POST to the NLP service with circuit breaker and retries."""
        if self._circuit.is_open:
            return None

        http = await self._get_http()
        url = f"{self._base_url}{path}"

        for attempt in range(1 + self._max_retries):
            try:
                resp = await http.post(url, json=payload)
                resp.raise_for_status()
                self._circuit.record_success()
                return resp.json()
            except (httpx.HTTPError, httpx.TimeoutException, Exception):
                if attempt == self._max_retries:
                    self._circuit.record_failure()
                    return None

        return None

    async def get_embeddings(self, texts: list[str]) -> list[list[float]] | None:
        """Get sentence embeddings for a batch of texts.

        Returns list of float vectors, or None if service unavailable.
        """
        if not texts:
            return []
        result = await self._post("/embed", {"texts": texts})
        if result is None:
            return None
        embeddings = result.get("embeddings")
        if not isinstance(embeddings, list):
            return None
        return embeddings

    async def get_emotion(self, text: str, language: str = "ru") -> dict[str, Any] | None:
        """Classify emotion of a single text.

        Routes to language-specific model (ru or en).
        Returns {"label": str, "score": float} or None if unavailable.
        """
        result = await self._post("/emotion", {"text": text, "language": language})
        if result is None:
            return None
        if "label" not in result:
            return None
        return result

    async def health_check(self) -> bool:
        """Check if the NLP service is healthy."""
        if self._circuit.is_open:
            return False
        try:
            http = await self._get_http()
            resp = await http.get(f"{self._base_url}/health")
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()


# ─── Similarity Helper ───────────────────────────────────────────────────────

async def semantic_similarity(
    text_a: str,
    text_b: str,
    client: NlpServiceClient | None = None,
) -> tuple[float, str]:
    """Compute similarity between two texts.

    Tries embedding service first, falls back to Jaccard on lemma sets.
    Returns (score, method) where method is "embedding" or "jaccard".
    """
    if client is not None:
        embeddings = await client.get_embeddings([text_a, text_b])
        if embeddings is not None and len(embeddings) == 2:
            score = cosine_similarity(embeddings[0], embeddings[1])
            return score, "embedding"

    # Fallback: Jaccard on spaCy lemma sets
    from .spacy_singleton import lemma_set as _lemma_set

    set_a = _lemma_set(text_a)
    set_b = _lemma_set(text_b)
    if not set_a or not set_b:
        return 0.0, "jaccard"
    score = len(set_a & set_b) / len(set_a | set_b)
    return score, "jaccard"
