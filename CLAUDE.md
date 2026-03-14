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

All code lives in `server.py`. Seven MCP tools are exposed via `FastMCP`:

**Analysis tools:**
- **`emotion_analyze`** — classifies emotion (Ekman) + 5-axis style vector
- **`emotion_de_escalate`** — rewrites a draft response to match a target style vector; session-aware or stateless
- **`emotion_evaluate_dialogue`** — evaluates a full dialogue: per-message vectors, trend, feedback loop risk, recommendations

**Session management tools:**
- **`session_create`** — create a stateful emotional tracking session with optional custom config
- **`session_get`** — retrieve session state (mode, config, turn count, history)
- **`session_reset`** — clear session history, optionally keep custom config
- **`session_configure`** — update session settings (target vectors, shift speed, thresholds)

All analysis tools accept optional `session_id` for stateful tracking. Without it, they work statelessly (backward-compatible).

**Session modes:**
- **Adaptive** (default) — mirrors user's style, gradually shifts toward a positive attractor vector (`adaptive_speed` fraction per turn)
- **De-escalation** — auto-activates on trigger emotions (anger/disgust/fear) + high A or E; applies stronger corrective shifts; cooldown before returning to adaptive

Key internal components:
- `EngineMode` — HOST (prompt generation) or API (direct LLM call)
- `StyleVector` (Pydantic model) — validated 5-axis vector, each axis integer -2 to +2
- `SessionState` / `SessionConfig` — in-memory session storage with auto-expiry
- `_host_analyze_prompt()` / `_host_de_escalate_prompt()` / `_host_evaluate_prompt()` — build self-contained prompts for host LLM
- `_compute_session_target()` — orchestrates mode detection and target vector computation
- `_compute_adaptive_target()` — blends user style toward attractor at configurable speed
- `_compute_target_vector()` — applies de-escalation shifts to a user's detected style
- `_llm_call()` — async Anthropic client (API mode only)
- `_parse_json_response()` — strips markdown fences from LLM output before JSON parsing

## Dependencies

Python >=3.11. Key packages: `mcp`, `anthropic`, `pydantic`, `httpx`. Managed with `uv` (see `uv.lock`).