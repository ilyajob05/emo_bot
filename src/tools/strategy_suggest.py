"""
strategy_suggest MCP tool — recommends dialogue strategy based on pattern detection.

Fully deterministic: no LLM calls, works offline, fast and predictable.
"""

from __future__ import annotations

import json

from ..models import StrategySuggestInput, StrategyResult
from ..pattern_detector import detect_all_patterns
from ..strategy_rules import suggest_strategy


async def strategy_suggest(params: StrategySuggestInput) -> str:
    """Analyze dialogue patterns and suggest a strategy for the next bot response.

    Runs deterministic pattern detection (repeated questions, escalation,
    legal threats, churn signals, etc.) and returns an actionable strategy
    with anti-patterns and escalation thresholds.

    No LLM calls — works identically in HOST and API mode.
    """
    patterns = detect_all_patterns(
        messages=params.dialogue_history,
        contacts_today=params.user_metadata.total_contacts_today,
    )

    result = suggest_strategy(
        messages=params.dialogue_history,
        patterns=patterns,
        available_actions=params.available_actions,
        user_metadata=params.user_metadata,
        language=params.language,
    )

    return json.dumps(result.model_dump(), indent=2, ensure_ascii=False)
