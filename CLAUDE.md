# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Emotional De-escalation MCP Server — a single-file MCP server (`server.py`) that analyzes emotional tone and rewrites responses using a 5-axis communication style model (Warmth, Formality, Playfulness, Assertiveness, Expressiveness). Supports two engine modes: HOST (structured prompts for the host LLM — free) and API (direct Anthropic API calls — autonomous).

## Commands

```bash
# Install dependencies (uses uv)
uv sync

# Run the MCP server (stdio transport)
python server.py

# Or via the entry point after install
pip install -e .
emotional-deescalation-mcp
```

Requires `ANTHROPIC_API_KEY` environment variable only in API mode. Host mode works without it.

## Engine Modes

Controlled via `EMOTION_MCP_MODE` env var or per-call `mode` parameter on each tool.

- **HOST** (default) — tools return structured prompts for the host LLM (Claude Desktop/Code/LM Studio) to execute. No external API calls. Free for the user.
- **API** — tools call Anthropic API directly. Requires `ANTHROPIC_API_KEY`. Falls back to HOST if key is missing.

## Architecture

### Legacy tools (server.py)

Original single-file MCP server with tonal analysis tools:

- **`emotion_analyze`** — classifies emotion (Ekman) + 5-axis style vector
- **`emotion_de_escalate`** — rewrites a draft response to match a target style vector
- **`emotion_evaluate_dialogue`** — evaluates a full dialogue: per-message vectors, trend, feedback loop risk
- **`session_create/get/reset/configure`** — stateful session management

### Phase 1: Strategic dialogue management (src/)

New tools focused on **dialogue strategy**, not just tone correction:

- **`strategy_suggest`** — deterministic pattern detection + strategy recommendation (no LLM needed)

Located in `src/` package:
- `src/models.py` — Pydantic models (DialogueMessage, StrategySuggestInput, StrategyResult, DetectedPattern, etc.)
- `src/pattern_detector.py` — deterministic pattern detectors (repeated questions, escalation, legal threats, churn, human requests, profanity, publicity threats, vulnerability, positive signals, repeated contact by keywords)
- `src/strategy_rules.py` — maps detected patterns to actionable strategies with anti-patterns and escalation thresholds
- `src/tools/strategy_suggest.py` — MCP tool wrapper
- `src/server.py` — standalone FastMCP server for new tools only

The `strategy_suggest` tool is also registered in the legacy `server.py` for backward compatibility.

### Key design decisions

- `strategy_suggest` is **fully deterministic** — no LLM calls, works offline, fast and predictable
- All new tools are **stateless** — dialogue history passed as parameters, no in-memory sessions
- Pattern detection uses **spaCy lemmatization** (ru_core_news_sm) + POS-based content word extraction + optional sentence embeddings via external service
- Keyword lists use **lemma forms** — one entry covers all inflected forms (e.g. "жалоба" matches "жалобу", "жалобой", "жалобы")
- Strategy rules use a priority system: legal_threat > profanity > human_request > publicity_threat > repeated_contact > repeated_question > no_progress > churn_signal > emotion_escalation > vulnerability > positive_signal

### NLP integration (`src/nlp/`)
- `spacy_singleton.py` — singleton spaCy loader, lemmatize(), lemma_set(), content_word_set(), contains_any_lemma()
- `clients.py` — async HTTP clients for external NLP service (embeddings + emotion), with circuit breaker and fallback. Supports two embedding backends: `NlpServiceClient` (custom nlp_service) and `LmStudioEmbeddingClient` (OpenAI-compatible /v1/embeddings, e.g. LM Studio with GGUF embedding models). Factory: `get_embedding_client()` selects backend via `NLP_EMBED_BACKEND` env var.
- `config.py` — environment variables for NLP services (NLP_SERVICE_URL, NLP_EMBED_BACKEND, LM_STUDIO_URL, LM_STUDIO_EMBED_MODEL, timeouts, circuit breaker settings, model names)

### External NLP service (`nlp_service/`)
- FastAPI app serving sentence embeddings and emotion classification
- Models: `multilingual-e5-base` (embeddings), two emotion models routed by language:
  - RU: `cointegrated/rubert-tiny2-cedr-emotion-detection` (~30MB, 6 Ekman emotions)
  - EN: `j-hartmann/emotion-english-distilroberta-base` (~82MB, 7 Ekman emotions)
- `/emotion` endpoint accepts `language` param ("ru"/"en") to route to the correct model
- Config: `NLP_EMOTION_MODEL_RU`, `NLP_EMOTION_MODEL_EN` env vars (see `src/nlp/config.py`)
- Endpoints: `POST /embed`, `POST /emotion`, `GET /health`
- Runs in Docker (all models pre-downloaded at build time) or locally via `uv sync --extra nlp && python -m nlp_service.app`
- **Optional** — MCP server works without it, falling back to spaCy-only detection
- Embeddings can alternatively be served by LM Studio (GGUF models, e.g. `multilingual-e5-base-Q8_0-GGUF`), but emotion classification requires this service (LM Studio doesn't support text-classification models)

### Legacy internal components
- `EngineMode` — HOST (prompt generation) or API (direct LLM call)
- `StyleVector` (Pydantic model) — validated 5-axis vector, each axis integer -2 to +2
- `_llm_call()` — async Anthropic client (API mode only)
- `_parse_json_response()` — strips markdown fences from LLM output before JSON parsing

## Dependencies

Python >=3.11. Key packages: `mcp`, `anthropic`, `pydantic`, `httpx`, `spacy`. Managed with `uv` (see `uv.lock`).

spaCy model: `ru_core_news_sm` (install via `uv pip install ru_core_news_sm@https://github.com/explosion/spacy-models/releases/download/ru_core_news_sm-3.8.0/ru_core_news_sm-3.8.0-py3-none-any.whl`)