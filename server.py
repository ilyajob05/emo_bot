"""
Emotional De-escalation MCP Server v2
======================================

A universal MCP server for emotional tone analysis and de-escalation
in customer support and any communication context.

Based on: https://medium.com/@ilyajob05/mistakes-to-avoid-when-developing-chatbots-for-user-support-5eefa21256ab

## 5-Axis Communication Style Model

Every message is characterized by a style vector of 5 independent axes,
each on a discrete scale: -2 (low), -1 (normal-), 0 (normal), +1 (normal+), +2 (high).

| Axis            | -2               | 0        | +2                  |
|-----------------|------------------|----------|---------------------|
| Warmth (W)      | cold, detached   | neutral  | warm, empathetic    |
| Formality (F)   | casual, slang    | balanced | formal, business    |
| Playfulness (P) | dead serious     | balanced | humorous, ironic    |
| Assertiveness(A)| uncertain, meek  | balanced | demanding, forceful |
| Expressiveness(E)| reserved, terse | balanced | emotional, intense  |

## Session Modes

The server supports stateful sessions with two operating modes:

- **Adaptive** (default): bot mirrors user's style but gradually shifts toward
  a positive "attractor" vector (configurable). Each turn moves adaptive_speed
  fraction of the distance toward the target. Creates natural, non-jarring
  convergence toward a constructive communication zone.

- **De-escalation**: activated automatically when user shows trigger emotions
  (anger, disgust, fear) combined with high assertiveness or expressiveness.
  Applies stronger corrective shifts. Reverts to adaptive after consecutive
  calm turns (cooldown).

Both modes support custom configuration: target vectors, shift speeds,
trigger thresholds, and per-axis de-escalation shifts.

## De-escalation Strategy

Per-axis shifts instead of a single coefficient:
- Assertiveness: shift toward 0 (reduce pressure)
- Expressiveness: shift toward 0 (calm intensity)
- Warmth: shift toward +1/+2 (increase empathy)
- Playfulness: shift toward 0 (avoid sarcasm risk)
- Formality: shift toward +1 (slightly more professional)

Requires: ANTHROPIC_API_KEY environment variable
"""

from __future__ import annotations

import json
import os
import re
import time
from enum import Enum
from typing import Any, Literal

import anthropic
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict, field_validator

from src.models import StrategySuggestInput
from src.tools.strategy_suggest import strategy_suggest as _strategy_suggest_impl


# ─── Domain Constants ────────────────────────────────────────────────────────

# Allowed axis values: discrete scale from -2 to +2
AXIS_MIN, AXIS_MAX = -2, 2

# Canonical axis names — order matters for compact display (W/F/P/A/E)
STYLE_AXIS_NAMES: tuple[str, ...] = (
    "warmth", "formality", "playfulness", "assertiveness", "expressiveness",
)

AXIS_SHORT: dict[str, str] = {
    "warmth": "W", "formality": "F", "playfulness": "P",
    "assertiveness": "A", "expressiveness": "E",
}

# Ekman basic emotions + neutral
VALID_EMOTIONS: set[str] = {
    "anger", "fear", "sadness", "happiness", "disgust", "surprise", "neutral",
}

VALID_TRENDS: set[str] = {
    "escalating", "de_escalating", "stable_negative", "stable_neutral", "stable_positive",
}
VALID_QUALITIES: set[str] = {"excellent", "good", "acceptable", "poor", "critical"}
VALID_RISKS: set[str] = {"none", "low", "medium", "high"}

# Default de-escalation shifts (applied to user's detected vector).
# Playfulness uses a special "toward zero" rule — see _shift_toward_zero().
DEFAULT_DE_ESCALATION_SHIFTS: dict[str, int] = {
    "warmth": +1,
    "formality": +1,
    "playfulness": 0,       # placeholder — actual logic in _compute_target_vector
    "assertiveness": -1,
    "expressiveness": -1,
}


# ─── Engine Mode ─────────────────────────────────────────────────────────────

class EngineMode(str, Enum):
    """Execution mode for analysis tools.

    HOST — return structured prompt for the host LLM (Claude Desktop/Code/LM Studio)
           to execute. No external API calls. Free for the user.
    API  — server calls LLM API directly (Anthropic, OpenAI-compatible, etc.).
           Requires API key. More autonomous.
    """
    HOST = "host"
    API = "api"


def _resolve_mode(per_call: str | None = None) -> EngineMode:
    """Resolve engine mode: per-call override → env var → default (host).

    Falls back to HOST if API mode requested but no ANTHROPIC_API_KEY is set.
    """
    if per_call and per_call.lower() in ("host", "api"):
        mode = EngineMode(per_call.lower())
    else:
        raw = os.environ.get("EMOTION_MCP_MODE", "host").lower()
        mode = EngineMode(raw) if raw in ("host", "api") else EngineMode.HOST

    if mode == EngineMode.API and not os.environ.get("ANTHROPIC_API_KEY"):
        mode = EngineMode.HOST

    return mode


# ─── LLM Configuration ──────────────────────────────────────────────────────

ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 2048

# Regex to strip ```json ... ``` fences from LLM output
_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)


# ─── Primitive Helpers ───────────────────────────────────────────────────────

def _clamp_axis(value: int) -> int:
    """Clamp an axis value to the valid range [-2, +2]."""
    return max(AXIS_MIN, min(AXIS_MAX, value))


def _coerce_axis_value(value: Any, axis_name: str) -> int:
    """Coerce a value to int within [-2, +2], raising ValueError on failure.

    Accepts int, float (rounded), or string-encoded numbers.
    Expected input: raw value from LLM JSON response.
    """
    if isinstance(value, bool):
        raise ValueError(f"Axis '{axis_name}': boolean is not a valid axis value")
    if isinstance(value, float):
        value = round(value)
    if isinstance(value, str):
        try:
            value = int(value)
        except (ValueError, TypeError):
            raise ValueError(f"Axis '{axis_name}': cannot convert {value!r} to int")
    if not isinstance(value, int):
        raise ValueError(f"Axis '{axis_name}': expected int, got {type(value).__name__}")
    return _clamp_axis(value)


def _shift_toward_zero(value: int) -> int:
    """Shift a value one step toward zero (for playfulness de-escalation)."""
    if value > 0:
        return _clamp_axis(value - 1)
    if value < 0:
        return _clamp_axis(value + 1)
    return 0


# ─── Style Vector ────────────────────────────────────────────────────────────

class StyleVector(BaseModel):
    """5-axis communication style vector. Each axis: integer in [-2, +2]."""
    model_config = ConfigDict(extra="forbid")

    warmth: int = Field(0, ge=AXIS_MIN, le=AXIS_MAX,
                        description="W: -2=cold/detached .. +2=warm/empathetic")
    formality: int = Field(0, ge=AXIS_MIN, le=AXIS_MAX,
                           description="F: -2=casual/slang .. +2=formal/business")
    playfulness: int = Field(0, ge=AXIS_MIN, le=AXIS_MAX,
                             description="P: -2=dead serious .. +2=humorous/ironic")
    assertiveness: int = Field(0, ge=AXIS_MIN, le=AXIS_MAX,
                               description="A: -2=uncertain/meek .. +2=demanding/forceful")
    expressiveness: int = Field(0, ge=AXIS_MIN, le=AXIS_MAX,
                                description="E: -2=reserved/terse .. +2=emotional/intense")

    def to_compact(self) -> str:
        """Format as 'W=+1 F=0 P=-1 A=0 E=+2'."""
        return " ".join(
            f"{AXIS_SHORT[a]}={getattr(self, a):+d}" for a in STYLE_AXIS_NAMES
        )

    def to_dict(self) -> dict[str, int]:
        """Export as {axis_name: int_value}."""
        return {a: getattr(self, a) for a in STYLE_AXIS_NAMES}


# ─── Session Management ─────────────────────────────────────────────────────

# Default "attractor" vector for adaptive mode — slightly warm, otherwise neutral
DEFAULT_ADAPTIVE_TARGET: dict[str, int] = {
    "warmth": +1, "formality": 0, "playfulness": 0,
    "assertiveness": 0, "expressiveness": 0,
}

# Emotions that trigger automatic switch to de-escalation mode
DEFAULT_TRIGGER_EMOTIONS: set[str] = {"anger", "disgust", "fear"}

# How many consecutive calm turns before switching back to adaptive
DE_ESCALATION_COOLDOWN_TURNS = 2


class SessionMode(str, Enum):
    ADAPTIVE = "adaptive"
    DE_ESCALATION = "de_escalation"


class SessionConfig(BaseModel):
    """Per-session configuration. All fields have defaults for zero-config usage."""
    model_config = ConfigDict(extra="forbid")

    adaptive_target: dict[str, int] = Field(
        default_factory=lambda: dict(DEFAULT_ADAPTIVE_TARGET),
        description="Target attractor vector for adaptive mode",
    )
    adaptive_speed: float = Field(
        default=0.3, ge=0.0, le=1.0,
        description="Fraction of distance to move toward attractor each turn (0=no shift, 1=instant)",
    )
    de_escalation_shifts: dict[str, int] = Field(
        default_factory=lambda: dict(DEFAULT_DE_ESCALATION_SHIFTS),
        description="Per-axis additive shifts for de-escalation mode",
    )
    de_escalation_emotion_triggers: list[str] = Field(
        default_factory=lambda: list(DEFAULT_TRIGGER_EMOTIONS),
        description="Emotions that trigger de-escalation mode",
    )
    de_escalation_axis_threshold: int = Field(
        default=1, ge=0, le=2,
        description="A or E value that (combined with trigger emotion) activates de-escalation",
    )
    timeout_seconds: int = Field(
        default=3600, ge=60,
        description="Session expiry timeout in seconds",
    )
    max_history: int = Field(
        default=200, ge=10,
        description="Max turns to keep in history (oldest trimmed)",
    )


class HistoryEntry(BaseModel):
    """Single turn recorded in session history."""
    role: str
    emotion: str
    style_vector: dict[str, int]
    text_preview: str = ""
    timestamp: float


class SessionState(BaseModel):
    """Full state for a conversation session."""
    session_id: str
    mode: SessionMode = SessionMode.ADAPTIVE
    config: SessionConfig = Field(default_factory=SessionConfig)
    history: list[HistoryEntry] = Field(default_factory=list)
    current_target: dict[str, int] = Field(
        default_factory=lambda: dict(DEFAULT_ADAPTIVE_TARGET),
    )
    created_at: float
    last_activity: float
    turn_count: int = 0
    _calm_streak: int = 0  # consecutive calm turns (for cooldown)


# Module-level session store
_sessions: dict[str, SessionState] = {}


def _create_session(
    session_id: str, config: SessionConfig | None = None,
) -> SessionState:
    """Create and store a new session."""
    now = time.time()
    cfg = config or SessionConfig()
    session = SessionState(
        session_id=session_id,
        config=cfg,
        current_target=dict(cfg.adaptive_target),
        created_at=now,
        last_activity=now,
    )
    _sessions[session_id] = session
    return session


def _get_session(session_id: str) -> SessionState | None:
    """Get session if it exists and hasn't expired."""
    session = _sessions.get(session_id)
    if session is None:
        return None
    if time.time() - session.last_activity > session.config.timeout_seconds:
        del _sessions[session_id]
        return None
    return session


def _get_or_create_session(session_id: str) -> SessionState:
    """Get existing session or create a new one."""
    session = _get_session(session_id)
    if session is None:
        session = _create_session(session_id)
    return session


def _cleanup_expired_sessions() -> int:
    """Remove expired sessions. Returns count removed."""
    now = time.time()
    expired = [
        sid for sid, s in _sessions.items()
        if now - s.last_activity > s.config.timeout_seconds
    ]
    for sid in expired:
        del _sessions[sid]
    return len(expired)


def _record_turn(
    session: SessionState, role: str, style_vector: dict[str, int],
    emotion: str, text_preview: str = "",
) -> None:
    """Record a turn in session history."""
    session.history.append(HistoryEntry(
        role=role, emotion=emotion, style_vector=style_vector,
        text_preview=text_preview[:100], timestamp=time.time(),
    ))
    session.turn_count += 1
    session.last_activity = time.time()
    # Trim history if needed
    if len(session.history) > session.config.max_history:
        session.history = session.history[-session.config.max_history:]


def _determine_mode(
    session: SessionState, user_emotion: str, user_vector: dict[str, int],
) -> SessionMode:
    """Determine operating mode based on user's current emotional state."""
    triggers = set(session.config.de_escalation_emotion_triggers)
    threshold = session.config.de_escalation_axis_threshold

    is_triggered = (
        user_emotion.lower() in triggers
        and (
            user_vector.get("assertiveness", 0) >= threshold
            or user_vector.get("expressiveness", 0) >= threshold
        )
    )

    if is_triggered:
        session._calm_streak = 0
        return SessionMode.DE_ESCALATION

    # If currently in de-escalation, require cooldown before switching back
    if session.mode == SessionMode.DE_ESCALATION:
        session._calm_streak += 1
        if session._calm_streak >= DE_ESCALATION_COOLDOWN_TURNS:
            session._calm_streak = 0
            return SessionMode.ADAPTIVE
        return SessionMode.DE_ESCALATION

    return SessionMode.ADAPTIVE


def _compute_adaptive_target(
    session: SessionState, user_vector: dict[str, int],
) -> dict[str, int]:
    """Compute target vector for adaptive mode.

    Blends between user's current style and the session's attractor,
    moving adaptive_speed fraction of the distance each turn.
    """
    speed = session.config.adaptive_speed
    attractor = session.config.adaptive_target
    target: dict[str, int] = {}
    for axis in STYLE_AXIS_NAMES:
        user_val = user_vector.get(axis, 0)
        attr_val = attractor.get(axis, 0)
        blended = user_val + speed * (attr_val - user_val)
        target[axis] = _clamp_axis(round(blended))
    return target


def _compute_session_target(
    session: SessionState, user_vector: dict[str, int], user_emotion: str,
) -> dict[str, int]:
    """Compute target vector using session state and mode logic."""
    session.mode = _determine_mode(session, user_emotion, user_vector)

    if session.mode == SessionMode.DE_ESCALATION:
        target = _compute_target_vector(
            user_vector, session.config.de_escalation_shifts,
        )
    else:
        target = _compute_adaptive_target(session, user_vector)

    session.current_target = target
    return target


def _session_summary(session: SessionState) -> dict[str, Any]:
    """Build a JSON-serializable summary of session state."""
    return {
        "session_id": session.session_id,
        "mode": session.mode.value,
        "turn_count": session.turn_count,
        "current_target": session.current_target,
        "config": {
            "adaptive_target": session.config.adaptive_target,
            "adaptive_speed": session.config.adaptive_speed,
            "de_escalation_shifts": session.config.de_escalation_shifts,
            "de_escalation_emotion_triggers": session.config.de_escalation_emotion_triggers,
            "de_escalation_axis_threshold": session.config.de_escalation_axis_threshold,
            "timeout_seconds": session.config.timeout_seconds,
            "max_history": session.config.max_history,
        },
        "history_length": len(session.history),
        "created_at": session.created_at,
        "last_activity": session.last_activity,
    }


# ─── Style Vector Validation (LLM output → verified dict) ───────────────────

def _validate_style_vector_dict(raw: dict[str, Any], context: str = "") -> dict[str, int]:
    """Validate and coerce a raw dict from LLM into a proper style vector.

    Expected input format: {"warmth": int, "formality": int, ...}
    Returns: dict with exactly STYLE_AXIS_NAMES keys, each value in [-2, +2].
    Missing axes default to 0.
    """
    prefix = f"[{context}] " if context else ""
    if not isinstance(raw, dict):
        raise ValueError(f"{prefix}style_vector must be a dict, got {type(raw).__name__}")

    result: dict[str, int] = {}
    for axis in STYLE_AXIS_NAMES:
        value = raw.get(axis, 0)
        result[axis] = _coerce_axis_value(value, f"{prefix}{axis}")
    return result


def _validate_analysis_response(data: dict[str, Any]) -> dict[str, Any]:
    """Validate structure of emotion_analyze LLM response.

    Expected format:
    {
        "emotion": str (one of VALID_EMOTIONS),
        "intensity": int (-2..+2),
        "style_vector": {axis: int, ...},
        "detected_style": str,
        "explanation": str,
        "triggers": list[str]
    }
    """
    assert isinstance(data, dict), f"Expected dict response, got {type(data).__name__}"

    # Emotion
    emotion = data.get("emotion", "neutral")
    if isinstance(emotion, str):
        emotion = emotion.lower().strip()
    if emotion not in VALID_EMOTIONS:
        emotion = "neutral"
    data["emotion"] = emotion

    # Intensity
    data["intensity"] = _coerce_axis_value(data.get("intensity", 0), "intensity")

    # Style vector
    raw_sv = data.get("style_vector", {})
    data["style_vector"] = _validate_style_vector_dict(
        raw_sv if isinstance(raw_sv, dict) else {}, context="analyze",
    )

    # Detected style — string fallback
    if not isinstance(data.get("detected_style"), str):
        data["detected_style"] = "unknown"

    # Explanation — string fallback
    if not isinstance(data.get("explanation"), str):
        data["explanation"] = ""

    # Triggers — list of strings
    triggers = data.get("triggers", [])
    if isinstance(triggers, list):
        data["triggers"] = [str(t) for t in triggers if t]
    else:
        data["triggers"] = []

    return data


def _validate_de_escalate_response(data: dict[str, Any]) -> dict[str, Any]:
    """Validate structure of emotion_de_escalate LLM response.

    Expected format:
    {
        "rewritten_text": str,
        "original_style_vector": {axis: int, ...},
        "result_style_vector": {axis: int, ...},
        "changes_applied": list[str]
    }
    """
    assert isinstance(data, dict), f"Expected dict response, got {type(data).__name__}"

    # Rewritten text — must be present and non-empty
    text = data.get("rewritten_text", "")
    assert isinstance(text, str) and len(text) > 0, "LLM returned empty rewritten_text"
    data["rewritten_text"] = text

    # Style vectors
    for key in ("original_style_vector", "result_style_vector"):
        raw_sv = data.get(key, {})
        data[key] = _validate_style_vector_dict(
            raw_sv if isinstance(raw_sv, dict) else {}, context=f"de_escalate.{key}",
        )

    # Changes applied
    changes = data.get("changes_applied", [])
    if isinstance(changes, list):
        data["changes_applied"] = [str(c) for c in changes if c]
    else:
        data["changes_applied"] = []

    return data


def _validate_dialogue_response(
    data: dict[str, Any], expected_count: int,
) -> dict[str, Any]:
    """Validate structure of emotion_evaluate_dialogue LLM response.

    Expected format:
    {
        "message_analyses": [{role, emotion, style_vector, detected_style}, ...],
        "overall_trend": str,
        "interaction_quality": str,
        "feedback_loop_risk": str,
        "style_dynamics": str,
        "recommendations": list[str]
    }

    Args:
        data: raw parsed dict from LLM
        expected_count: number of messages in original dialogue (for cross-check)
    """
    assert isinstance(data, dict), f"Expected dict response, got {type(data).__name__}"

    # Message analyses
    analyses = data.get("message_analyses", [])
    assert isinstance(analyses, list), "message_analyses must be a list"
    if len(analyses) != expected_count:
        # LLM may skip or merge messages; warn but don't fail
        pass

    validated_analyses = []
    for i, ma in enumerate(analyses):
        if not isinstance(ma, dict):
            continue
        entry: dict[str, Any] = {}

        entry["role"] = ma.get("role", "user") if isinstance(ma.get("role"), str) else "user"

        emotion = ma.get("emotion", "neutral")
        entry["emotion"] = emotion if isinstance(emotion, str) and emotion.lower() in VALID_EMOTIONS else "neutral"

        raw_sv = ma.get("style_vector", {})
        entry["style_vector"] = _validate_style_vector_dict(
            raw_sv if isinstance(raw_sv, dict) else {},
            context=f"dialogue.message[{i}]",
        )

        entry["detected_style"] = str(ma.get("detected_style", "unknown"))
        validated_analyses.append(entry)

    data["message_analyses"] = validated_analyses

    # Enum-like fields with fallbacks
    trend = data.get("overall_trend", "stable_neutral")
    data["overall_trend"] = trend if trend in VALID_TRENDS else "stable_neutral"

    quality = data.get("interaction_quality", "acceptable")
    data["interaction_quality"] = quality if quality in VALID_QUALITIES else "acceptable"

    risk = data.get("feedback_loop_risk", "low")
    data["feedback_loop_risk"] = risk if risk in VALID_RISKS else "low"

    # Free-text fields
    if not isinstance(data.get("style_dynamics"), str):
        data["style_dynamics"] = ""
    recommendations = data.get("recommendations", [])
    if isinstance(recommendations, list):
        data["recommendations"] = [str(r) for r in recommendations if r]
    else:
        data["recommendations"] = []

    return data


# ─── De-escalation Logic ────────────────────────────────────────────────────

def _compute_target_vector(
    user_vector: dict[str, int],
    shifts: dict[str, int] | None = None,
) -> dict[str, int]:
    """Compute de-escalated target style vector from user's detected vector.

    Strategy per axis:
    - playfulness: shift toward 0 (reduce sarcasm/irony risk)
    - all others: apply additive shift from `shifts` dict, clamped to [-2, +2]

    Args:
        user_vector: validated style vector dict {axis: int}
        shifts: per-axis additive shifts (defaults to DEFAULT_DE_ESCALATION_SHIFTS)

    Returns:
        dict with same keys as STYLE_AXIS_NAMES, values in [-2, +2]
    """
    s = shifts if shifts is not None else DEFAULT_DE_ESCALATION_SHIFTS

    target: dict[str, int] = {}
    for axis in STYLE_AXIS_NAMES:
        user_val = user_vector.get(axis, 0)
        if axis == "playfulness":
            target[axis] = _shift_toward_zero(user_val)
        else:
            target[axis] = _clamp_axis(user_val + s.get(axis, 0))
    return target


# ─── Shared Enums & Input Models ─────────────────────────────────────────────

class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


class DialogueMessage(BaseModel):
    """A single message in a dialogue."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    role: Literal["user", "bot", "operator"] = Field(
        ..., description="Message author role",
    )
    text: str = Field(
        ..., description="Message text", min_length=1, max_length=10_000,
    )


# ─── Markdown Formatting ────────────────────────────────────────────────────

def _sv_md(sv: dict[str, int], label: str = "Style") -> str:
    """Format a style vector dict as a compact Markdown line.

    Expected input: {axis_name: int} with all STYLE_AXIS_NAMES present.
    """
    parts = " ".join(f"{AXIS_SHORT[a]}={sv.get(a, 0):+d}" for a in STYLE_AXIS_NAMES)
    return f"**{label}:** {parts}"


def _format_analyze(data: dict[str, Any], fmt: ResponseFormat) -> str:
    """Format validated analysis result as JSON or Markdown."""
    if fmt == ResponseFormat.JSON:
        return json.dumps(data, indent=2, ensure_ascii=False)

    lines = [
        "## Emotional Analysis\n",
        f"**Emotion:** {data['emotion']}",
        f"**Intensity:** {data['intensity']:+d}",
    ]
    if data.get("style_vector"):
        lines.append(_sv_md(data["style_vector"]))
    if data.get("detected_style"):
        lines.append(f"**Detected style:** {data['detected_style']}")
    if data.get("explanation"):
        lines.append(f"\n{data['explanation']}")
    if data.get("triggers"):
        lines.append(f"\n**Triggers:** {', '.join(data['triggers'])}")

    return "\n".join(lines) + "\n"


def _format_de_escalate(data: dict[str, Any], fmt: ResponseFormat) -> str:
    """Format validated de-escalation result as JSON or Markdown."""
    if fmt == ResponseFormat.JSON:
        return json.dumps(data, indent=2, ensure_ascii=False)

    lines = [
        "## De-escalated Response\n",
        data.get("rewritten_text", ""),
        "\n---\n",
    ]
    for key, label in (
        ("user_style_vector", "User style"),
        ("original_style_vector", "Draft style"),
        ("target_style_vector", "Target style"),
        ("result_style_vector", "Achieved style"),
    ):
        if data.get(key):
            lines.append(_sv_md(data[key], label))

    if data.get("changes_applied"):
        lines.append("\n**Changes:**")
        for ch in data["changes_applied"]:
            lines.append(f"- {ch}")

    return "\n".join(lines) + "\n"


def _format_dialogue(data: dict[str, Any], fmt: ResponseFormat) -> str:
    """Format validated dialogue evaluation result as JSON or Markdown."""
    if fmt == ResponseFormat.JSON:
        return json.dumps(data, indent=2, ensure_ascii=False)

    lines = [
        "## Dialogue Dynamics\n",
        f"**Trend:** {data['overall_trend']}",
        f"**Quality:** {data['interaction_quality']}",
        f"**Feedback loop risk:** {data['feedback_loop_risk']}\n",
    ]

    if data.get("message_analyses"):
        lines.append("| # | Role | Emotion | W | F | P | A | E | Style |")
        lines.append("|---|------|---------|---|---|---|---|---|-------|")
        for i, ma in enumerate(data["message_analyses"], 1):
            sv = ma.get("style_vector", {})
            lines.append(
                f"| {i} | {ma['role']} | {ma['emotion']} "
                f"| {sv.get('warmth', 0):+d} | {sv.get('formality', 0):+d} "
                f"| {sv.get('playfulness', 0):+d} | {sv.get('assertiveness', 0):+d} "
                f"| {sv.get('expressiveness', 0):+d} "
                f"| {ma.get('detected_style', '')} |"
            )
        lines.append("")

    if data.get("style_dynamics"):
        lines.append(f"**Dynamics:** {data['style_dynamics']}\n")
    if data.get("recommendations"):
        lines.append("### Recommendations\n")
        for rec in data["recommendations"]:
            lines.append(f"- {rec}")

    return "\n".join(lines) + "\n"


# ─── System Prompts ──────────────────────────────────────────────────────────

STYLE_VECTOR_SPEC = """
## 5-Axis Communication Style Vector

Each axis is an integer from -2 to +2:

| Axis             | -2               | 0        | +2                  |
|------------------|------------------|----------|---------------------|
| warmth (W)       | cold, detached   | neutral  | warm, empathetic    |
| formality (F)    | casual, slang    | balanced | formal, business    |
| playfulness (P)  | dead serious     | balanced | humorous, ironic    |
| assertiveness (A)| uncertain, meek  | balanced | demanding, forceful |
| expressiveness(E)| reserved, terse  | balanced | emotional, intense  |

Reference patterns:
- Sarcasm:           W=-2, F=-1, P=+2, A= 0, E=+1
- Friendly humor:    W=+1, F=-1, P=+2, A=-1, E= 0
- Flirtatious:       W=+2, F=-2, P=+1, A=-1, E=+1
- Business tone:     W= 0, F=+2, P=-2, A= 0, E=-1
- Aggression:        W=-2, F=-2, P=-2, A=+2, E=+2
- Desperation:       W= 0, F=-1, P=-2, A=-1, E=+2
- Passive-aggression:W=-1, F= 0, P=+1, A=+1, E=-1

CRITICAL: All values MUST be integers from the set {-2, -1, 0, 1, 2}. No floats.
"""

ANALYZE_SYSTEM = f"""\
You are an expert in emotional tone and communication style analysis.

{STYLE_VECTOR_SPEC}

Respond with ONLY a JSON object (no markdown fences):
{{
  "emotion": "anger"|"fear"|"sadness"|"happiness"|"disgust"|"surprise"|"neutral",
  "intensity": int -2 to +2,
  "style_vector": {{"warmth": int, "formality": int, "playfulness": int, "assertiveness": int, "expressiveness": int}},
  "detected_style": short label (e.g. "sarcasm", "friendly", "aggressive", "business", "desperate", "passive-aggressive"),
  "explanation": brief reason,
  "triggers": [specific words/phrases]
}}

Detect sarcasm (high P + low W), passive-aggression (mid P + mid A + low W),
and distinguish genuine humor from hostile irony.
"""

DE_ESCALATE_SYSTEM = f"""\
You are an expert in conflict de-escalation.

{STYLE_VECTOR_SPEC}

Rewrite the draft response to match the TARGET style vector provided.
Rules:
1. Match the target vector as closely as possible
2. Acknowledge feelings without mirroring aggression
3. Stay factual and concise
4. No passive-aggressive or sarcastic undertones
5. Max one apology/acknowledgment
6. Preserve all factual content from the draft

Respond with ONLY a JSON object (no markdown fences):
{{
  "rewritten_text": "...",
  "original_style_vector": {{"warmth": int, "formality": int, "playfulness": int, "assertiveness": int, "expressiveness": int}},
  "result_style_vector": {{"warmth": int, "formality": int, "playfulness": int, "assertiveness": int, "expressiveness": int}},
  "changes_applied": ["change 1", "change 2"]
}}

IMPORTANT: Write in the SAME language as the draft.
"""

EVALUATE_SYSTEM = f"""\
You are an expert in dialogue dynamics analysis.

{STYLE_VECTOR_SPEC}

For EACH message determine emotion and style vector.
Then analyze overall dynamics.

Respond with ONLY a JSON object (no markdown fences):
{{
  "message_analyses": [
    {{
      "role": "user"|"bot"|"operator",
      "emotion": "anger"|"fear"|"sadness"|"happiness"|"disgust"|"surprise"|"neutral",
      "style_vector": {{"warmth": int, "formality": int, "playfulness": int, "assertiveness": int, "expressiveness": int}},
      "detected_style": "label"
    }}
  ],
  "overall_trend": "escalating"|"de_escalating"|"stable_negative"|"stable_neutral"|"stable_positive",
  "interaction_quality": "excellent"|"good"|"acceptable"|"poor"|"critical",
  "feedback_loop_risk": "none"|"low"|"medium"|"high",
  "style_dynamics": "description of how W/F/P/A/E evolved",
  "recommendations": ["rec 1", "rec 2"]
}}

Detect positive feedback loops: bot mirroring user's high A/E or low W.
Detect sarcasm escalation: P increasing while W decreasing.
"""


# ─── Host Mode Prompt Builders ──────────────────────────────────────────────
# In HOST mode, tools return a structured prompt for the host LLM to execute.
# The prompt contains the full expertise (5-axis model, rules, session context)
# so the host LLM produces the analysis in a single pass.

def _host_analyze_prompt(
    text: str,
    context: str | None = None,
    language_hint: str | None = None,
    session: Any = None,
) -> str:
    """Build a self-contained analysis prompt for the host LLM."""
    parts = [
        ANALYZE_SYSTEM,
        "\n---\n",
    ]

    if session is not None:
        parts.append(_session_context_block(session))

    if context:
        parts.append(f"Dialogue context:\n{context}\n\n---\n")

    parts.append(f"Analyze this message:\n\n{text}")

    if language_hint:
        parts.append(f"\n\nLanguage: {language_hint}")

    return "\n".join(parts)


def _host_de_escalate_prompt(
    user_message: str,
    draft_response: str,
    target_dict: dict[str, int],
    user_style_dict: dict[str, int] | None = None,
    dialogue_history: str | None = None,
    preserve_facts: bool = True,
    session: Any = None,
    language_hint: str | None = None,
) -> str:
    """Build a self-contained de-escalation prompt for the host LLM.

    Includes analysis + de-escalation + recommendations in one prompt.
    """
    target_spec = "\n".join(f"  {a}={target_dict[a]:+d}" for a in STYLE_AXIS_NAMES)

    parts = [
        DE_ESCALATE_SYSTEM,
        "\n---\n",
    ]

    if session is not None:
        parts.append(_session_context_block(session))

    if user_style_dict is not None:
        user_spec = " ".join(
            f"{AXIS_SHORT[a]}={user_style_dict.get(a, 0):+d}" for a in STYLE_AXIS_NAMES
        )
        parts.append(f"User's detected style: {user_spec}\n")

    if dialogue_history:
        parts.append(f"Dialogue history:\n{dialogue_history}\n\n---\n")

    parts.append(
        f"User's message:\n{user_message}\n\n"
        f"Draft response to rewrite:\n{draft_response}\n\n"
        f"TARGET style vector:\n{target_spec}\n\n"
        f"Preserve facts: {preserve_facts}\n\n"
        "Also provide:\n"
        "1. Brief analysis of the user's emotional state\n"
        "2. Explanation of why each style axis was shifted\n"
        "3. Recommendations for continuing the conversation"
    )

    if language_hint:
        parts.append(f"\n\nLanguage: {language_hint}")

    return "\n".join(parts)


def _host_evaluate_prompt(
    messages: list[Any],
    session: Any = None,
) -> str:
    """Build a self-contained dialogue evaluation prompt for the host LLM."""
    text = "\n".join(f"{m.role}: {m.text}" for m in messages)

    parts = [
        EVALUATE_SYSTEM,
        "\n---\n",
    ]

    if session is not None:
        parts.append(_session_context_block(session))

    parts.append(f"Analyze this dialogue:\n\n{text}")

    return "\n".join(parts)


def _session_context_block(session: Any) -> str:
    """Format session state as context for host LLM prompts."""
    sv = session.current_target
    target_str = " ".join(
        f"{AXIS_SHORT[a]}={sv.get(a, 0):+d}" for a in STYLE_AXIS_NAMES
    )
    lines = [
        "## Session Context\n",
        f"Mode: {session.mode.value}",
        f"Turn: {session.turn_count}",
        f"Current target vector: {target_str}",
    ]

    # Last few turns for context
    recent = session.history[-5:] if session.history else []
    if recent:
        lines.append("\nRecent history:")
        for entry in recent:
            sv_str = " ".join(
                f"{AXIS_SHORT[a]}={entry.style_vector.get(a, 0):+d}"
                for a in STYLE_AXIS_NAMES
            )
            lines.append(f"  [{entry.role}] {entry.emotion} | {sv_str}")

    lines.append("")
    return "\n".join(lines)


# ─── LLM Client ─────────────────────────────────────────────────────────────

_async_client: anthropic.AsyncAnthropic | None = None


def _get_async_client() -> anthropic.AsyncAnthropic:
    """Lazy-initialize the async Anthropic client."""
    global _async_client
    if _async_client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY environment variable is not set. "
                "Set it before running the server: export ANTHROPIC_API_KEY=sk-ant-..."
            )
        _async_client = anthropic.AsyncAnthropic(api_key=api_key)
    return _async_client


async def _llm_call(system_prompt: str, user_prompt: str) -> str:
    """Make an async Anthropic API call and return the text response.

    Args:
        system_prompt: system-level instruction
        user_prompt: user-level message

    Returns:
        Raw text from the first content block of the response.

    Raises:
        anthropic.APIError: on API-level failures
        RuntimeError: if response has no text content
    """
    client = _get_async_client()
    response = await client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    if not response.content or not hasattr(response.content[0], "text"):
        raise RuntimeError("LLM returned empty or non-text response")

    return response.content[0].text


def _parse_json_response(raw: str) -> dict[str, Any]:
    """Parse JSON from LLM response, stripping markdown code fences if present.

    Handles: bare JSON, ```json...```, ```...```
    Returns: parsed dict
    Raises: json.JSONDecodeError on malformed JSON
    """
    cleaned = raw.strip()
    fence_match = _FENCE_RE.match(cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    result = json.loads(cleaned)
    assert isinstance(result, dict), f"Expected JSON object at top level, got {type(result).__name__}"
    return result


# ─── MCP Server & Tools ─────────────────────────────────────────────────────

mcp = FastMCP("emotional_deescalation_mcp")


# ── Tool 1: Analyze ──

class AnalyzeInput(BaseModel):
    """Input for emotional tone and style analysis."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    text: str = Field(
        ..., description="Message to analyze", min_length=1, max_length=10_000,
    )
    context: str | None = Field(
        default=None, description="Previous messages for context", max_length=20_000,
    )
    language_hint: str | None = Field(
        default=None, description="ISO 639-1 code (e.g. 'en', 'ru')", max_length=5,
    )
    session_id: str | None = Field(
        default=None,
        description="Session ID for stateful tracking. Omit for stateless operation.",
        max_length=128,
    )
    mode: str | None = Field(
        default=None,
        description="Engine mode: 'host' (prompt for host LLM, default) or 'api' (direct API call).",
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.JSON)


@mcp.tool(
    name="emotion_analyze",
    annotations={
        "title": "Analyze Emotional Tone & Communication Style",
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": True,
    },
)
async def emotion_analyze(params: AnalyzeInput) -> str:
    """Analyze emotional tone and 5-axis style vector of a message.

    In HOST mode: returns a structured prompt for the host LLM to execute.
    In API mode: calls LLM API directly and returns parsed results.

    Returns emotion category (Ekman), 5-axis style vector (W/F/P/A/E, each -2..+2),
    detected style label, explanation, and trigger words.
    """
    engine = _resolve_mode(params.mode)

    # Resolve session (needed for both modes)
    session = None
    if params.session_id is not None:
        _cleanup_expired_sessions()
        session = _get_or_create_session(params.session_id)

    # HOST mode — return structured prompt for the host LLM
    if engine == EngineMode.HOST:
        prompt = _host_analyze_prompt(
            params.text, params.context, params.language_hint, session,
        )
        return prompt

    # API mode — call LLM directly, parse and validate
    prompt = f"Analyze this message:\n\n{params.text}"
    if params.context:
        prompt = f"Dialogue context:\n{params.context}\n\n---\n\n{prompt}"
    if params.language_hint:
        prompt += f"\n\nLanguage: {params.language_hint}"

    try:
        raw = await _llm_call(ANALYZE_SYSTEM, prompt)
        data = _parse_json_response(raw)
        data = _validate_analysis_response(data)
    except json.JSONDecodeError:
        return json.dumps({"error": "Failed to parse LLM response", "raw": raw})
    except Exception as e:
        return json.dumps({"error": f"Analysis failed: {type(e).__name__}: {e}"})

    # Session tracking (API mode only — in host mode, host manages flow)
    if session is not None:
        _record_turn(
            session, "user", data["style_vector"],
            data["emotion"], params.text,
        )
        data["session"] = {
            "session_id": session.session_id,
            "mode": session.mode.value,
            "turn_count": session.turn_count,
        }

    data["engine"] = engine.value
    return _format_analyze(data, params.response_format)


# ── Tool 2: De-escalate ──

class DeEscalateInput(BaseModel):
    """Input for style-targeted de-escalation."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_message: str = Field(
        ..., description="User's message (context)", min_length=1, max_length=10_000,
    )
    draft_response: str = Field(
        ..., description="Draft to de-escalate", min_length=1, max_length=10_000,
    )
    dialogue_history: str | None = Field(
        default=None, description="Full dialogue history", max_length=30_000,
    )
    target_style: StyleVector | None = Field(
        default=None,
        description="Explicit target style override. If omitted, auto-computed via session/de-escalation rules.",
    )
    preserve_facts: bool = Field(
        default=True, description="Preserve all factual content",
    )
    language_hint: str | None = Field(
        default=None, description="ISO 639-1 code (e.g. 'en', 'ru')", max_length=5,
    )
    session_id: str | None = Field(
        default=None,
        description="Session ID for stateful tracking. Omit for stateless operation.",
        max_length=128,
    )
    mode: str | None = Field(
        default=None,
        description="Engine mode: 'host' (prompt for host LLM, default) or 'api' (direct API call).",
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.JSON)


@mcp.tool(
    name="emotion_de_escalate",
    annotations={
        "title": "De-escalate Response with Style Targeting",
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": True,
    },
)
async def emotion_de_escalate(params: DeEscalateInput) -> str:
    """Rewrite a response to match target 5-axis style, breaking feedback loops.

    In HOST mode: returns a self-contained prompt that includes analysis +
    de-escalation + recommendations for the host LLM to execute in one pass.
    In API mode: calls LLM API directly (two calls: analyze + rewrite).

    Auto mode: analyzes user's style, applies shifts (W+1, F+1, A-1, E-1, P→0).
    Override mode: caller provides explicit target_style vector.
    """
    engine = _resolve_mode(params.mode)

    # Resolve session
    session: SessionState | None = None
    if params.session_id is not None:
        _cleanup_expired_sessions()
        session = _get_or_create_session(params.session_id)

    # Compute target vector (needed for both modes)
    user_style_dict: dict[str, int] | None = None
    user_emotion: str = "neutral"

    if params.target_style is not None:
        target_dict = params.target_style.to_dict()
    else:
        # Use default de-escalation shifts for target computation
        # In API mode, will also analyze user's style via LLM
        if engine == EngineMode.HOST:
            # In host mode, use default shifts — the host LLM will do the analysis
            default_user = {a: 0 for a in STYLE_AXIS_NAMES}
            if session is not None:
                target_dict = _compute_session_target(session, default_user, "neutral")
            else:
                target_dict = _compute_target_vector(default_user)
        else:
            # API mode — analyze user's style first
            try:
                raw_a = await _llm_call(
                    ANALYZE_SYSTEM, f"Analyze this message:\n\n{params.user_message}",
                )
                analysis = _parse_json_response(raw_a)
                analysis = _validate_analysis_response(analysis)
                user_style_dict = analysis["style_vector"]
                user_emotion = analysis.get("emotion", "neutral")
            except Exception:
                user_style_dict = {a: 0 for a in STYLE_AXIS_NAMES}

            if session is not None:
                target_dict = _compute_session_target(
                    session, user_style_dict, user_emotion,
                )
            else:
                target_dict = _compute_target_vector(user_style_dict)

    # HOST mode — return self-contained prompt
    if engine == EngineMode.HOST:
        prompt = _host_de_escalate_prompt(
            params.user_message, params.draft_response, target_dict,
            user_style_dict, params.dialogue_history, params.preserve_facts,
            session, params.language_hint,
        )
        return prompt

    # API mode — call LLM, parse, validate
    target_spec = "\n".join(f"  {a}={target_dict[a]:+d}" for a in STYLE_AXIS_NAMES)
    prompt = (
        f"User's message:\n{params.user_message}\n\n"
        f"Draft response to rewrite:\n{params.draft_response}\n\n"
        f"TARGET style vector:\n{target_spec}\n\n"
        f"Preserve facts: {params.preserve_facts}"
    )
    if params.language_hint:
        prompt += f"\n\nLanguage: {params.language_hint}"
    if params.dialogue_history:
        prompt = f"Dialogue history:\n{params.dialogue_history}\n\n---\n\n{prompt}"

    try:
        raw = await _llm_call(DE_ESCALATE_SYSTEM, prompt)
        data = _parse_json_response(raw)
        data = _validate_de_escalate_response(data)
        data["target_style_vector"] = target_dict
        if user_style_dict is not None:
            data["user_style_vector"] = user_style_dict
    except json.JSONDecodeError:
        return json.dumps({"error": "Failed to parse LLM response", "raw": raw})
    except Exception as e:
        return json.dumps({"error": f"De-escalation failed: {type(e).__name__}: {e}"})

    # Session tracking (API mode)
    if session is not None:
        if user_style_dict is not None:
            _record_turn(
                session, "user", user_style_dict,
                user_emotion, params.user_message,
            )
        _record_turn(
            session, "bot", data.get("result_style_vector", target_dict),
            "neutral", data.get("rewritten_text", "")[:100],
        )
        data["session"] = {
            "session_id": session.session_id,
            "mode": session.mode.value,
            "turn_count": session.turn_count,
        }

    data["engine"] = engine.value
    return _format_de_escalate(data, params.response_format)


# ── Tool 3: Evaluate Dialogue ──

class EvaluateDialogueInput(BaseModel):
    """Input for dialogue dynamics evaluation."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    messages: list[DialogueMessage] = Field(
        ..., description="Chronological dialogue messages",
        min_length=2, max_length=100,
    )
    session_id: str | None = Field(
        default=None,
        description="Session ID for stateful tracking. Omit for stateless operation.",
        max_length=128,
    )
    mode: str | None = Field(
        default=None,
        description="Engine mode: 'host' (prompt for host LLM, default) or 'api' (direct API call).",
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.JSON)

    @field_validator("messages")
    @classmethod
    def validate_has_user(cls, v: list[DialogueMessage]) -> list[DialogueMessage]:
        if not any(m.role == "user" for m in v):
            raise ValueError("Dialogue must contain at least one user message")
        return v


@mcp.tool(
    name="emotion_evaluate_dialogue",
    annotations={
        "title": "Evaluate Dialogue Emotional Dynamics",
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": True,
    },
)
async def emotion_evaluate_dialogue(params: EvaluateDialogueInput) -> str:
    """Evaluate emotional dynamics and style evolution of a full dialogue.

    In HOST mode: returns a structured prompt for the host LLM.
    In API mode: calls LLM API directly and returns parsed results.

    Per-message: emotion + 5-axis style vector + style label.
    Overall: trend, quality, feedback loop risk, style dynamics, recommendations.
    """
    engine = _resolve_mode(params.mode)

    # Resolve session
    session = None
    if params.session_id is not None:
        _cleanup_expired_sessions()
        session = _get_or_create_session(params.session_id)

    # HOST mode — return structured prompt
    if engine == EngineMode.HOST:
        prompt = _host_evaluate_prompt(params.messages, session)
        return prompt

    # API mode — call LLM directly
    text = "\n".join(f"{m.role}: {m.text}" for m in params.messages)
    message_count = len(params.messages)

    try:
        raw = await _llm_call(EVALUATE_SYSTEM, f"Analyze this dialogue:\n\n{text}")
        data = _parse_json_response(raw)
        data = _validate_dialogue_response(data, expected_count=message_count)
    except json.JSONDecodeError:
        return json.dumps({"error": "Failed to parse LLM response", "raw": raw})
    except Exception as e:
        return json.dumps({"error": f"Evaluation failed: {type(e).__name__}: {e}"})

    # Session tracking (API mode)
    if session is not None:
        for i, ma in enumerate(data.get("message_analyses", [])):
            msg = params.messages[i] if i < len(params.messages) else None
            _record_turn(
                session, ma.get("role", "user"), ma.get("style_vector", {}),
                ma.get("emotion", "neutral"),
                msg.text if msg else "",
            )
        data["session"] = {
            "session_id": session.session_id,
            "mode": session.mode.value,
            "turn_count": session.turn_count,
        }

    data["engine"] = engine.value
    return _format_dialogue(data, params.response_format)


# ── Tool 4: Session Create ──

class SessionCreateInput(BaseModel):
    """Input for creating a new session."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    session_id: str = Field(
        ..., description="Unique session identifier", min_length=1, max_length=128,
    )
    config: SessionConfig | None = Field(
        default=None,
        description="Custom session configuration. Omit for defaults.",
    )


@mcp.tool(
    name="session_create",
    annotations={
        "title": "Create Emotional Tracking Session",
        "readOnlyHint": False, "destructiveHint": False,
        "idempotentHint": False, "openWorldHint": False,
    },
)
async def session_create(params: SessionCreateInput) -> str:
    """Create a new session for stateful emotional tracking.

    Sessions track style vectors over time and automatically switch between
    adaptive mode (gradual shift toward positive) and de-escalation mode
    (corrective shifts when user is agitated).

    Custom config allows tuning: target attractor, shift speed, trigger thresholds.
    """
    _cleanup_expired_sessions()
    existing = _get_session(params.session_id)
    if existing is not None:
        return json.dumps({
            "error": f"Session '{params.session_id}' already exists. Use session_reset to clear it.",
            "session": _session_summary(existing),
        }, indent=2, ensure_ascii=False)

    session = _create_session(params.session_id, params.config)
    return json.dumps({
        "status": "created",
        "session": _session_summary(session),
    }, indent=2, ensure_ascii=False)


# ── Tool 5: Session Get ──

class SessionGetInput(BaseModel):
    """Input for retrieving session state."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    session_id: str = Field(
        ..., description="Session ID to retrieve", min_length=1, max_length=128,
    )
    include_history: bool = Field(
        default=False, description="Include full turn history in response",
    )


@mcp.tool(
    name="session_get",
    annotations={
        "title": "Get Session State",
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    },
)
async def session_get(params: SessionGetInput) -> str:
    """Get current state of an emotional tracking session.

    Returns mode, config, turn count, current target vector, and optionally full history.
    """
    _cleanup_expired_sessions()
    session = _get_session(params.session_id)
    if session is None:
        return json.dumps({"error": f"Session '{params.session_id}' not found or expired"})

    result = _session_summary(session)
    if params.include_history:
        result["history"] = [
            {
                "role": e.role, "emotion": e.emotion,
                "style_vector": e.style_vector,
                "text_preview": e.text_preview,
            }
            for e in session.history
        ]
    return json.dumps(result, indent=2, ensure_ascii=False)


# ── Tool 6: Session Reset ──

class SessionResetInput(BaseModel):
    """Input for resetting a session."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    session_id: str = Field(
        ..., description="Session ID to reset", min_length=1, max_length=128,
    )
    keep_config: bool = Field(
        default=True, description="Keep custom config (True) or reset to defaults (False)",
    )


@mcp.tool(
    name="session_reset",
    annotations={
        "title": "Reset Session",
        "readOnlyHint": False, "destructiveHint": True,
        "idempotentHint": True, "openWorldHint": False,
    },
)
async def session_reset(params: SessionResetInput) -> str:
    """Reset a session: clear history and turn count, revert to adaptive mode.

    With keep_config=True, preserves custom configuration.
    With keep_config=False, resets everything to defaults.
    """
    _cleanup_expired_sessions()
    session = _get_session(params.session_id)
    if session is None:
        return json.dumps({"error": f"Session '{params.session_id}' not found or expired"})

    config = session.config if params.keep_config else None
    _create_session(params.session_id, config)
    new_session = _sessions[params.session_id]

    return json.dumps({
        "status": "reset",
        "session": _session_summary(new_session),
    }, indent=2, ensure_ascii=False)


# ── Tool 7: Session Configure ──

class SessionConfigureInput(BaseModel):
    """Input for updating session configuration."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    session_id: str = Field(
        ..., description="Session ID to configure", min_length=1, max_length=128,
    )
    config: SessionConfig = Field(
        ..., description="New session configuration",
    )


@mcp.tool(
    name="session_configure",
    annotations={
        "title": "Configure Session Settings",
        "readOnlyHint": False, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    },
)
async def session_configure(params: SessionConfigureInput) -> str:
    """Update session configuration: target vectors, shift speed, thresholds.

    Creates the session if it doesn't exist.
    """
    _cleanup_expired_sessions()
    session = _get_or_create_session(params.session_id)
    session.config = params.config
    session.current_target = dict(params.config.adaptive_target)
    session.last_activity = time.time()

    return json.dumps({
        "status": "configured",
        "session": _session_summary(session),
    }, indent=2, ensure_ascii=False)


# ─── Strategy Suggest (Phase 1 — deterministic) ─────────────────────────────

@mcp.tool(
    name="strategy_suggest",
    annotations={
        "title": "Suggest Dialogue Strategy",
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
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
    return await _strategy_suggest_impl(params)


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()