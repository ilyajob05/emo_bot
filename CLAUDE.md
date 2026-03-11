# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Emotional De-escalation MCP Server — a single-file MCP server (`server.py`) that analyzes emotional tone and rewrites responses using a 5-axis communication style model (Warmth, Formality, Playfulness, Assertiveness, Expressiveness). Uses the Anthropic Claude API as the analysis backend.

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

Requires `ANTHROPIC_API_KEY` environment variable.

## Architecture

All code lives in `server.py`. Three MCP tools are exposed via `FastMCP`:

- **`emotion_analyze`** — classifies emotion (Ekman) + 5-axis style vector
- **`emotion_de_escalate`** — rewrites a draft response to match a target style vector; auto mode analyzes user's style then applies per-axis shifts (W+1, F+1, A-1, E-1, P→0)
- **`emotion_evaluate_dialogue`** — evaluates a full dialogue: per-message vectors, trend, feedback loop risk, recommendations

Each tool sends a structured prompt to the Anthropic API, parses the JSON response, and formats output as JSON or Markdown based on `response_format`.

Key internal components:
- `StyleVector` (Pydantic model) — validated 5-axis vector, each axis integer -2 to +2
- `_compute_target_vector()` — applies de-escalation shifts to a user's detected style
- `_llm_call()` — lazy-initialized Anthropic client, synchronous `messages.create` wrapped in async
- `_parse_json_response()` — strips markdown fences from LLM output before JSON parsing

## Dependencies

Python >=3.11. Key packages: `mcp`, `anthropic`, `pydantic`, `httpx`. Managed with `uv` (see `uv.lock`).