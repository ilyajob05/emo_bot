"""
Pydantic models for the Support Intelligence MCP Server.

Covers dialogue context, pattern detection results, strategy recommendations,
and style vectors reused from the original emotional de-escalation server.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict, field_validator


# ─── Domain Constants ────────────────────────────────────────────────────────

AXIS_MIN, AXIS_MAX = -2, 2

STYLE_AXIS_NAMES: tuple[str, ...] = (
    "warmth", "formality", "playfulness", "assertiveness", "expressiveness",
)

AXIS_SHORT: dict[str, str] = {
    "warmth": "W", "formality": "F", "playfulness": "P",
    "assertiveness": "A", "expressiveness": "E",
}


# ─── Style Vector ────────────────────────────────────────────────────────────

class StyleVector(BaseModel):
    """5-axis communication style vector. Each axis: integer in [-2, +2]."""
    model_config = ConfigDict(extra="forbid")

    warmth: int = Field(0, ge=AXIS_MIN, le=AXIS_MAX)
    formality: int = Field(0, ge=AXIS_MIN, le=AXIS_MAX)
    playfulness: int = Field(0, ge=AXIS_MIN, le=AXIS_MAX)
    assertiveness: int = Field(0, ge=AXIS_MIN, le=AXIS_MAX)
    expressiveness: int = Field(0, ge=AXIS_MIN, le=AXIS_MAX)

    def to_compact(self) -> str:
        return " ".join(
            f"{AXIS_SHORT[a]}={getattr(self, a):+d}" for a in STYLE_AXIS_NAMES
        )

    def to_dict(self) -> dict[str, int]:
        return {a: getattr(self, a) for a in STYLE_AXIS_NAMES}


# ─── Dialogue Models ─────────────────────────────────────────────────────────

class DialogueMessage(BaseModel):
    """A single message in a dialogue history."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    role: Literal["user", "bot", "operator"] = Field(
        ..., description="Message author role",
    )
    text: str = Field(
        ..., description="Message text", min_length=1, max_length=10_000,
    )
    timestamp: str | None = Field(
        default=None, description="ISO 8601 timestamp (optional)",
    )


class UserMetadata(BaseModel):
    """Optional metadata about the user contacting support."""
    model_config = ConfigDict(extra="allow")

    total_contacts_today: int = Field(
        default=1, ge=0, description="How many times the user contacted support today",
    )
    previous_tickets: bool = Field(
        default=False, description="Whether the user has unresolved tickets",
    )
    vip: bool = Field(
        default=False, description="VIP/priority customer flag",
    )


# ─── Pattern Detection Results ───────────────────────────────────────────────

class DetectedPattern(BaseModel):
    """A single detected dialogue pattern."""
    pattern_type: str = Field(
        ..., description="Pattern identifier (e.g. 'repeated_question', 'legal_threat')",
    )
    severity: Literal["info", "warning", "critical"] = Field(
        ..., description="How urgent this pattern is",
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Detection confidence",
    )
    evidence: list[str] = Field(
        default_factory=list, description="Specific evidence (quotes, counts)",
    )
    details: dict[str, Any] = Field(
        default_factory=dict, description="Extra structured data",
    )


# ─── Strategy Models ─────────────────────────────────────────────────────────

class ActionStep(BaseModel):
    """A single recommended action within a strategy."""
    action: str = Field(
        ..., description="Action identifier (e.g. 'acknowledge_frustration', 'lookup_by_phone')",
    )
    priority: Literal["required", "primary", "fallback"] = Field(
        ..., description="How important this action is",
    )
    note: str = Field(
        default="", description="Guidance for executing this action",
    )


class EscalationThreshold(BaseModel):
    """When to escalate to a human agent."""
    should_escalate_now: bool = Field(
        default=False, description="Whether to escalate immediately",
    )
    escalate_after_n_more_turns: int | None = Field(
        default=None, description="Escalate if no resolution after N more turns",
    )
    reason: str = Field(
        default="", description="Why escalation is recommended",
    )


class StrategyResult(BaseModel):
    """Full output of strategy_suggest."""
    recommended_strategy: str = Field(
        ..., description="Strategy name (e.g. 'alternative_identification', 'immediate_escalation')",
    )
    reasoning: str = Field(
        ..., description="Why this strategy was chosen",
    )
    action_sequence: list[ActionStep] = Field(
        default_factory=list, description="Ordered list of actions to take",
    )
    anti_patterns: list[str] = Field(
        default_factory=list, description="Things the bot must NOT do",
    )
    escalation: EscalationThreshold = Field(
        default_factory=EscalationThreshold,
    )
    detected_patterns: list[DetectedPattern] = Field(
        default_factory=list, description="Patterns that triggered this strategy",
    )


# ─── Tool Input Models ───────────────────────────────────────────────────────

class StrategySuggestInput(BaseModel):
    """Input for strategy_suggest tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    dialogue_history: list[DialogueMessage] = Field(
        ..., description="Full dialogue history",
        min_length=1, max_length=200,
    )
    user_metadata: UserMetadata = Field(
        default_factory=UserMetadata,
        description="Optional user context (contacts today, VIP, etc.)",
    )
    available_actions: list[str] = Field(
        default_factory=list,
        description="Actions the bot can perform (e.g. 'lookup_by_phone', 'escalate_to_human')",
    )
    bot_capabilities: dict[str, bool] = Field(
        default_factory=dict,
        description="What the bot can do (e.g. {'has_db_access': true, 'can_escalate': true})",
    )
    language: str = Field(
        default="ru", description="ISO 639-1 language code for anti-patterns and notes",
        max_length=5,
    )

    @field_validator("dialogue_history")
    @classmethod
    def must_have_user_message(cls, v: list[DialogueMessage]) -> list[DialogueMessage]:
        if not any(m.role == "user" for m in v):
            raise ValueError("dialogue_history must contain at least one user message")
        return v
