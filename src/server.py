"""
Support Intelligence MCP Server
================================

MCP server providing strategic dialogue management tools for support bots.
Phase 1: strategy_suggest (deterministic pattern detection + strategy rules).

Run: python -m src.server
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .models import StrategySuggestInput
from .tools.strategy_suggest import strategy_suggest as _strategy_suggest

mcp = FastMCP("support_intelligence_mcp")


@mcp.tool(
    name="strategy_suggest",
    annotations={
        "title": "Suggest Dialogue Strategy",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def strategy_suggest(params: StrategySuggestInput) -> str:
    """Analyze dialogue patterns and suggest a strategy for the next bot response.

    Detects problematic patterns (repeated questions, escalation, legal threats,
    churn signals) and recommends concrete actions, anti-patterns, and escalation
    thresholds. Fully deterministic — no LLM calls, works offline.

    Input: dialogue_history (required), user_metadata, available_actions,
    bot_capabilities, language.

    Output: recommended_strategy, reasoning, action_sequence, anti_patterns,
    escalation thresholds, detected_patterns.
    """
    return await _strategy_suggest(params)


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
