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
from enum import Enum
from typing import Any, Literal

import anthropic
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict, field_validator


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

    Returns emotion category (Ekman), 5-axis style vector (W/F/P/A/E, each -2..+2),
    detected style label, explanation, and trigger words.

    Args:
        params: text, optional context, language_hint, response_format

    Returns:
        Analysis with emotion, style_vector, triggers (JSON or Markdown)
    """
    # Build prompt: optional context → main text → optional language hint
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
        description="Explicit target style override. If omitted, auto-computed via de-escalation rules.",
    )
    preserve_facts: bool = Field(
        default=True, description="Preserve all factual content",
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

    Auto mode: analyzes user's style vector, applies shifts (W+1, F+1, A-1, E-1, P→0).
    Override mode: caller provides explicit target_style vector.

    Args:
        params: user_message, draft_response, optional target_style/history

    Returns:
        Rewritten text + user/original/target/achieved style vectors + change log
    """
    # Phase 1: determine target vector
    user_style_dict: dict[str, int] | None = None

    if params.target_style is not None:
        target_dict = params.target_style.to_dict()
    else:
        # Auto mode — analyze user's style, then apply de-escalation shifts
        try:
            raw_a = await _llm_call(
                ANALYZE_SYSTEM, f"Analyze this message:\n\n{params.user_message}",
            )
            analysis = _parse_json_response(raw_a)
            analysis = _validate_analysis_response(analysis)
            user_style_dict = analysis["style_vector"]
        except Exception:
            user_style_dict = {a: 0 for a in STYLE_AXIS_NAMES}
        target_dict = _compute_target_vector(user_style_dict)

    # Phase 2: build rewrite prompt
    target_spec = "\n".join(f"  {a}={target_dict[a]:+d}" for a in STYLE_AXIS_NAMES)
    prompt = (
        f"User's message:\n{params.user_message}\n\n"
        f"Draft response to rewrite:\n{params.draft_response}\n\n"
        f"TARGET style vector:\n{target_spec}\n\n"
        f"Preserve facts: {params.preserve_facts}"
    )
    if params.dialogue_history:
        prompt = f"Dialogue history:\n{params.dialogue_history}\n\n---\n\n{prompt}"

    # Phase 3: call LLM, validate, attach computed vectors
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

    return _format_de_escalate(data, params.response_format)


# ── Tool 3: Evaluate Dialogue ──

class EvaluateDialogueInput(BaseModel):
    """Input for dialogue dynamics evaluation."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    messages: list[DialogueMessage] = Field(
        ..., description="Chronological dialogue messages",
        min_length=2, max_length=100,
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

    Per-message: emotion + 5-axis style vector + style label.
    Overall: trend, quality, feedback loop risk, style dynamics, recommendations.

    Args:
        params: messages list, response_format

    Returns:
        Table of per-message vectors, trend analysis, recommendations
    """
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

    return _format_dialogue(data, params.response_format)


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()