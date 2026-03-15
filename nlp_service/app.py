"""
NLP Service — FastAPI application serving embeddings and emotion classification.

Loads three models at startup:
- multilingual-e5-base (sentence embeddings, 118MB)
- rubert-tiny2-cedr-emotion-detection (Russian emotion, ~30MB)
- emotion-english-distilroberta-base (English emotion, ~82MB)

Emotion endpoint routes to the appropriate model based on `language` parameter.

Run: uvicorn nlp_service.app:app --host 0.0.0.0 --port 8100
"""

from __future__ import annotations

import sys
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# Allow importing src.nlp.config when running from the project root
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.nlp.config import NLP_EMBED_MODEL, NLP_EMOTION_MODEL_RU, NLP_EMOTION_MODEL_EN

logger = logging.getLogger("nlp_service")

# ─── Models (loaded at startup) ──────────────────────────────────────────────

_embed_model = None
_emotion_pipe_ru = None
_emotion_pipe_en = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load models on startup, clean up on shutdown."""
    global _embed_model, _emotion_pipe_ru, _emotion_pipe_en

    logger.info("Loading embedding model: %s", NLP_EMBED_MODEL)
    try:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer(NLP_EMBED_MODEL)
        logger.info("Embedding model loaded")
    except Exception as e:
        logger.error("Failed to load embedding model: %s", e)

    logger.info("Loading RU emotion model: %s", NLP_EMOTION_MODEL_RU)
    try:
        from transformers import pipeline
        _emotion_pipe_ru = pipeline(
            "text-classification",
            model=NLP_EMOTION_MODEL_RU,
            top_k=1,
        )
        logger.info("RU emotion model loaded")
    except Exception as e:
        logger.error("Failed to load RU emotion model: %s", e)

    logger.info("Loading EN emotion model: %s", NLP_EMOTION_MODEL_EN)
    try:
        from transformers import pipeline as _pipeline
        _emotion_pipe_en = _pipeline(
            "text-classification",
            model=NLP_EMOTION_MODEL_EN,
            top_k=1,
        )
        logger.info("EN emotion model loaded")
    except Exception as e:
        logger.error("Failed to load EN emotion model: %s", e)

    yield

    _embed_model = None
    _emotion_pipe_ru = None
    _emotion_pipe_en = None


app = FastAPI(title="NLP Service", lifespan=lifespan)


# ─── Request/Response Schemas ─────────────────────────────────────────────────

class EmbedRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1, max_length=100)


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]


class EmotionRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    language: str = Field(default="ru", pattern=r"^(ru|en)$")


class EmotionResponse(BaseModel):
    label: str
    score: float


class HealthResponse(BaseModel):
    status: str
    models: dict[str, bool]


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
def health():
    all_loaded = _embed_model and _emotion_pipe_ru and _emotion_pipe_en
    return HealthResponse(
        status="ok" if all_loaded else "degraded",
        models={
            "embedding": _embed_model is not None,
            "emotion_ru": _emotion_pipe_ru is not None,
            "emotion_en": _emotion_pipe_en is not None,
        },
    )


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest):
    if _embed_model is None:
        raise HTTPException(503, "Embedding model not loaded")

    # multilingual-e5 requires "query: " prefix for best results
    prefixed = [f"query: {t}" for t in req.texts]
    embeddings = _embed_model.encode(prefixed, normalize_embeddings=True)
    return EmbedResponse(embeddings=embeddings.tolist())


@app.post("/emotion", response_model=EmotionResponse)
def emotion(req: EmotionRequest):
    pipe = _emotion_pipe_ru if req.language == "ru" else _emotion_pipe_en

    if pipe is None:
        raise HTTPException(503, f"Emotion model for '{req.language}' not loaded")

    results = pipe(req.text[:512])  # truncate to model limit
    if not results or not results[0]:
        raise HTTPException(500, "Emotion classification failed")

    top = results[0][0] if isinstance(results[0], list) else results[0]
    return EmotionResponse(label=top["label"], score=round(top["score"], 4))


def main():
    """Run the NLP service locally (without Docker)."""
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "nlp_service.app:app",
        host="0.0.0.0",
        port=8100,
    )


if __name__ == "__main__":
    main()
