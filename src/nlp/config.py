"""Configuration for NLP services — environment variables with defaults."""

from __future__ import annotations

import os


# ─── spaCy ────────────────────────────────────────────────────────────────────

SPACY_MODEL: str = os.environ.get("SPACY_MODEL", "ru_core_news_sm")

# ─── External NLP service (Phases 2-3) ───────────────────────────────────────

NLP_SERVICE_URL: str = os.environ.get("NLP_SERVICE_URL", "http://localhost:8100")
NLP_REQUEST_TIMEOUT: float = float(os.environ.get("NLP_REQUEST_TIMEOUT", "2.0"))
NLP_MAX_RETRIES: int = int(os.environ.get("NLP_MAX_RETRIES", "1"))
NLP_CIRCUIT_BREAKER_THRESHOLD: int = int(os.environ.get("NLP_CIRCUIT_BREAKER_THRESHOLD", "5"))
NLP_CIRCUIT_BREAKER_RESET: float = float(os.environ.get("NLP_CIRCUIT_BREAKER_RESET", "30.0"))

# ─── Embedding backend ────────────────────────────────────────────────────

# Options: "nlp_service" (default), "lmstudio" (OpenAI-compatible /v1/embeddings)
NLP_EMBED_BACKEND: str = os.environ.get("NLP_EMBED_BACKEND", "nlp_service")
LM_STUDIO_URL: str = os.environ.get("LM_STUDIO_URL", "http://localhost:1234")
LM_STUDIO_EMBED_MODEL: str = os.environ.get("LM_STUDIO_EMBED_MODEL", "")

# ─── Model names ─────────────────────────────────────────────────────────────

NLP_EMBED_MODEL: str = os.environ.get("NLP_EMBED_MODEL", "intfloat/multilingual-e5-base")

# Emotion models — two specialized models routed by language
NLP_EMOTION_MODEL_RU: str = os.environ.get(
    "NLP_EMOTION_MODEL_RU", "cointegrated/rubert-tiny2-cedr-emotion-detection"
)
NLP_EMOTION_MODEL_EN: str = os.environ.get(
    "NLP_EMOTION_MODEL_EN", "j-hartmann/emotion-english-distilroberta-base"
)

# Backward compatibility
NLP_EMOTION_MODEL: str = NLP_EMOTION_MODEL_RU
