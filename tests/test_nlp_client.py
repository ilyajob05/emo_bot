"""Tests for NLP service client — circuit breaker, fallbacks, cosine similarity."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.nlp.clients import (
    CircuitBreaker,
    NlpServiceClient,
    cosine_similarity,
    semantic_similarity,
)


# ─── Cosine Similarity ───────────────────────────────────────────────────────


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_empty_vectors(self):
        assert cosine_similarity([], []) == 0.0

    def test_zero_vector(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_different_lengths(self):
        assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0


# ─── Circuit Breaker ─────────────────────────────────────────────────────────


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker(threshold=3, reset_seconds=10.0)
        assert cb.is_open is False

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(threshold=3, reset_seconds=10.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is False
        cb.record_failure()
        assert cb.is_open is True

    def test_success_resets_count(self):
        cb = CircuitBreaker(threshold=3, reset_seconds=10.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        assert cb.is_open is False

    def test_half_open_after_reset(self):
        cb = CircuitBreaker(threshold=1, reset_seconds=0.0)  # instant reset
        cb.record_failure()
        # With reset_seconds=0, circuit immediately transitions to half-open
        # on the next is_open check, so it returns False (allowing a probe)
        assert cb.is_open is False  # half-open = allows one probe


# ─── NLP Service Client ──────────────────────────────────────────────────────


class TestNlpServiceClient:
    @pytest.mark.asyncio
    async def test_get_embeddings_success(self):
        client = NlpServiceClient(base_url="http://test:8100")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
        }

        with patch.object(client, "_get_http") as mock_http:
            mock_http_client = AsyncMock()
            mock_http_client.post.return_value = mock_response
            mock_http.return_value = mock_http_client

            result = await client.get_embeddings(["hello", "world"])
            assert result is not None
            assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_embeddings_service_down(self):
        client = NlpServiceClient(base_url="http://test:8100", max_retries=0)

        with patch.object(client, "_get_http") as mock_http:
            mock_http_client = AsyncMock()
            mock_http_client.post.side_effect = ConnectionError("refused")
            mock_http.return_value = mock_http_client

            result = await client.get_embeddings(["hello"])
            assert result is None

    @pytest.mark.asyncio
    async def test_get_emotion_success(self):
        client = NlpServiceClient(base_url="http://test:8100")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"label": "anger", "score": 0.92}

        with patch.object(client, "_get_http") as mock_http:
            mock_http_client = AsyncMock()
            mock_http_client.post.return_value = mock_response
            mock_http.return_value = mock_http_client

            result = await client.get_emotion("Я в ярости!")
            assert result is not None
            assert result["label"] == "anger"
            assert result["score"] == 0.92

    @pytest.mark.asyncio
    async def test_get_emotion_english(self):
        client = NlpServiceClient(base_url="http://test:8100")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"label": "anger", "score": 0.88}

        with patch.object(client, "_get_http") as mock_http:
            mock_http_client = AsyncMock()
            mock_http_client.post.return_value = mock_response
            mock_http.return_value = mock_http_client

            result = await client.get_emotion("I am furious!", language="en")
            assert result is not None
            assert result["label"] == "anger"
            # Verify language was passed in the request payload
            call_args = mock_http_client.post.call_args
            assert call_args[1]["json"]["language"] == "en"

    @pytest.mark.asyncio
    async def test_circuit_breaker_prevents_calls(self):
        client = NlpServiceClient(
            base_url="http://test:8100",
            max_retries=0,
            circuit_threshold=1,
        )

        with patch.object(client, "_get_http") as mock_http:
            mock_http_client = AsyncMock()
            mock_http_client.post.side_effect = ConnectionError("refused")
            mock_http.return_value = mock_http_client

            # First call fails and opens circuit
            result1 = await client.get_embeddings(["test"])
            assert result1 is None

            # Second call should not even attempt HTTP
            result2 = await client.get_embeddings(["test"])
            assert result2 is None
            # Only 1 HTTP call (not 2)
            assert mock_http_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_empty_texts(self):
        client = NlpServiceClient()
        result = await client.get_embeddings([])
        assert result == []


# ─── Semantic Similarity ─────────────────────────────────────────────────────


class TestSemanticSimilarity:
    @pytest.mark.asyncio
    async def test_with_embeddings(self):
        client = NlpServiceClient(base_url="http://test:8100")

        with patch.object(client, "get_embeddings") as mock_embed:
            mock_embed.return_value = [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]

            score, method = await semantic_similarity("a", "b", client)
            assert method == "embedding"
            assert score == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_fallback_to_jaccard(self):
        client = NlpServiceClient(base_url="http://test:8100")

        with patch.object(client, "get_embeddings") as mock_embed:
            mock_embed.return_value = None  # service unavailable

            score, method = await semantic_similarity(
                "номер заказа", "номер заказа", client,
            )
            assert method == "jaccard"
            assert score > 0.5  # same text should be similar

    @pytest.mark.asyncio
    async def test_no_client_uses_jaccard(self):
        score, method = await semantic_similarity(
            "номер заказа", "номер заказа", None,
        )
        assert method == "jaccard"
        assert score > 0.5
