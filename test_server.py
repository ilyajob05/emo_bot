"""Unit tests for server.py — Emotional De-escalation MCP Server."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from pydantic import ValidationError

from server import (
    AXIS_MIN,
    AXIS_MAX,
    STYLE_AXIS_NAMES,
    VALID_EMOTIONS,
    VALID_TRENDS,
    VALID_QUALITIES,
    VALID_RISKS,
    ResponseFormat,
    StyleVector,
    DialogueMessage,
    AnalyzeInput,
    DeEscalateInput,
    EvaluateDialogueInput,
    _clamp_axis,
    _coerce_axis_value,
    _shift_toward_zero,
    _compute_target_vector,
    _validate_style_vector_dict,
    _validate_analysis_response,
    _validate_de_escalate_response,
    _validate_dialogue_response,
    _parse_json_response,
    _sv_md,
    _format_analyze,
    _format_de_escalate,
    _format_dialogue,
    emotion_analyze,
    emotion_de_escalate,
    emotion_evaluate_dialogue,
)


# ─── _clamp_axis ─────────────────────────────────────────────────────────────


class TestClampAxis:
    def test_within_range(self):
        for v in range(AXIS_MIN, AXIS_MAX + 1):
            assert _clamp_axis(v) == v

    def test_below_min(self):
        assert _clamp_axis(-5) == AXIS_MIN
        assert _clamp_axis(-3) == AXIS_MIN

    def test_above_max(self):
        assert _clamp_axis(5) == AXIS_MAX
        assert _clamp_axis(3) == AXIS_MAX


# ─── _coerce_axis_value ─────────────────────────────────────────────────────


class TestCoerceAxisValue:
    def test_int_in_range(self):
        assert _coerce_axis_value(1, "warmth") == 1

    def test_int_clamped(self):
        assert _coerce_axis_value(10, "warmth") == AXIS_MAX
        assert _coerce_axis_value(-10, "warmth") == AXIS_MIN

    def test_float_rounded(self):
        assert _coerce_axis_value(1.6, "warmth") == 2
        assert _coerce_axis_value(-0.4, "warmth") == 0

    def test_string_number(self):
        assert _coerce_axis_value("1", "warmth") == 1
        assert _coerce_axis_value("-2", "warmth") == -2

    def test_string_invalid(self):
        with pytest.raises(ValueError, match="cannot convert"):
            _coerce_axis_value("abc", "warmth")

    def test_boolean_rejected(self):
        with pytest.raises(ValueError, match="boolean"):
            _coerce_axis_value(True, "warmth")

    def test_none_rejected(self):
        with pytest.raises(ValueError, match="expected int"):
            _coerce_axis_value(None, "warmth")

    def test_list_rejected(self):
        with pytest.raises(ValueError, match="expected int"):
            _coerce_axis_value([1], "warmth")


# ─── _shift_toward_zero ─────────────────────────────────────────────────────


class TestShiftTowardZero:
    def test_positive(self):
        assert _shift_toward_zero(2) == 1
        assert _shift_toward_zero(1) == 0

    def test_negative(self):
        assert _shift_toward_zero(-2) == -1
        assert _shift_toward_zero(-1) == 0

    def test_zero(self):
        assert _shift_toward_zero(0) == 0


# ─── StyleVector ─────────────────────────────────────────────────────────────


class TestStyleVector:
    def test_defaults(self):
        sv = StyleVector()
        for axis in STYLE_AXIS_NAMES:
            assert getattr(sv, axis) == 0

    def test_valid_values(self):
        sv = StyleVector(warmth=2, formality=-2, playfulness=1, assertiveness=-1, expressiveness=0)
        assert sv.warmth == 2
        assert sv.formality == -2

    def test_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            StyleVector(warmth=3)
        with pytest.raises(ValidationError):
            StyleVector(formality=-3)

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            StyleVector(warmth=0, unknown=1)

    def test_to_compact(self):
        sv = StyleVector(warmth=1, formality=-1, playfulness=0, assertiveness=2, expressiveness=-2)
        result = sv.to_compact()
        assert "W=+1" in result
        assert "F=-1" in result
        assert "P=+0" in result
        assert "A=+2" in result
        assert "E=-2" in result

    def test_to_dict(self):
        sv = StyleVector(warmth=1, formality=-1)
        d = sv.to_dict()
        assert d == {
            "warmth": 1, "formality": -1, "playfulness": 0,
            "assertiveness": 0, "expressiveness": 0,
        }


# ─── _validate_style_vector_dict ────────────────────────────────────────────


class TestValidateStyleVectorDict:
    def test_valid(self):
        raw = {"warmth": 1, "formality": -1, "playfulness": 0, "assertiveness": 2, "expressiveness": -2}
        result = _validate_style_vector_dict(raw)
        assert result == raw

    def test_missing_axes_default_to_zero(self):
        result = _validate_style_vector_dict({"warmth": 1})
        assert result["formality"] == 0
        assert result["playfulness"] == 0

    def test_coerces_values(self):
        result = _validate_style_vector_dict({"warmth": "1", "formality": 1.7})
        assert result["warmth"] == 1
        assert result["formality"] == 2

    def test_clamps_values(self):
        result = _validate_style_vector_dict({"warmth": 5})
        assert result["warmth"] == 2

    def test_non_dict_raises(self):
        with pytest.raises(ValueError, match="must be a dict"):
            _validate_style_vector_dict("not a dict")

    def test_context_in_error(self):
        with pytest.raises(ValueError, match="myctx"):
            _validate_style_vector_dict({"warmth": True}, context="myctx")


# ─── _validate_analysis_response ────────────────────────────────────────────


class TestValidateAnalysisResponse:
    def _base_response(self) -> dict:
        return {
            "emotion": "anger",
            "intensity": 1,
            "style_vector": {"warmth": -1, "formality": 0, "playfulness": 0, "assertiveness": 1, "expressiveness": 1},
            "detected_style": "aggressive",
            "explanation": "User is upset.",
            "triggers": ["terrible", "worst"],
        }

    def test_valid_passthrough(self):
        data = self._base_response()
        result = _validate_analysis_response(data)
        assert result["emotion"] == "anger"
        assert result["intensity"] == 1
        assert result["style_vector"]["warmth"] == -1

    def test_unknown_emotion_defaults_neutral(self):
        data = self._base_response()
        data["emotion"] = "confusion"
        result = _validate_analysis_response(data)
        assert result["emotion"] == "neutral"

    def test_missing_fields_have_defaults(self):
        result = _validate_analysis_response({})
        assert result["emotion"] == "neutral"
        assert result["intensity"] == 0
        assert result["detected_style"] == "unknown"
        assert result["explanation"] == ""
        assert result["triggers"] == []

    def test_triggers_non_list(self):
        data = self._base_response()
        data["triggers"] = "single trigger"
        result = _validate_analysis_response(data)
        assert result["triggers"] == []

    def test_non_dict_raises(self):
        with pytest.raises(AssertionError):
            _validate_analysis_response("not a dict")


# ─── _validate_de_escalate_response ─────────────────────────────────────────


class TestValidateDeEscalateResponse:
    def _base_response(self) -> dict:
        sv = {"warmth": 1, "formality": 1, "playfulness": 0, "assertiveness": 0, "expressiveness": 0}
        return {
            "rewritten_text": "I understand your concern.",
            "original_style_vector": sv.copy(),
            "result_style_vector": sv.copy(),
            "changes_applied": ["Reduced assertiveness", "Added empathy"],
        }

    def test_valid(self):
        data = self._base_response()
        result = _validate_de_escalate_response(data)
        assert result["rewritten_text"] == "I understand your concern."

    def test_empty_rewritten_text_raises(self):
        data = self._base_response()
        data["rewritten_text"] = ""
        with pytest.raises(AssertionError, match="empty rewritten_text"):
            _validate_de_escalate_response(data)

    def test_missing_rewritten_text_raises(self):
        data = self._base_response()
        del data["rewritten_text"]
        with pytest.raises(AssertionError, match="empty rewritten_text"):
            _validate_de_escalate_response(data)

    def test_changes_applied_non_list(self):
        data = self._base_response()
        data["changes_applied"] = "single change"
        result = _validate_de_escalate_response(data)
        assert result["changes_applied"] == []


# ─── _validate_dialogue_response ────────────────────────────────────────────


class TestValidateDialogueResponse:
    def _base_response(self) -> dict:
        sv = {"warmth": 0, "formality": 0, "playfulness": 0, "assertiveness": 0, "expressiveness": 0}
        return {
            "message_analyses": [
                {"role": "user", "emotion": "anger", "style_vector": sv.copy(), "detected_style": "aggressive"},
                {"role": "bot", "emotion": "neutral", "style_vector": sv.copy(), "detected_style": "professional"},
            ],
            "overall_trend": "stable_neutral",
            "interaction_quality": "acceptable",
            "feedback_loop_risk": "low",
            "style_dynamics": "Stable throughout.",
            "recommendations": ["Keep calm", "Be empathetic"],
        }

    def test_valid(self):
        result = _validate_dialogue_response(self._base_response(), expected_count=2)
        assert len(result["message_analyses"]) == 2

    def test_invalid_trend_defaults(self):
        data = self._base_response()
        data["overall_trend"] = "unknown_trend"
        result = _validate_dialogue_response(data, expected_count=2)
        assert result["overall_trend"] == "stable_neutral"

    def test_invalid_quality_defaults(self):
        data = self._base_response()
        data["interaction_quality"] = "amazing"
        result = _validate_dialogue_response(data, expected_count=2)
        assert result["interaction_quality"] == "acceptable"

    def test_invalid_risk_defaults(self):
        data = self._base_response()
        data["feedback_loop_risk"] = "extreme"
        result = _validate_dialogue_response(data, expected_count=2)
        assert result["feedback_loop_risk"] == "low"

    def test_non_dict_messages_skipped(self):
        data = self._base_response()
        data["message_analyses"] = ["not a dict", {"role": "user", "emotion": "neutral", "style_vector": {}, "detected_style": "ok"}]
        result = _validate_dialogue_response(data, expected_count=2)
        assert len(result["message_analyses"]) == 1

    def test_recommendations_non_list(self):
        data = self._base_response()
        data["recommendations"] = "single rec"
        result = _validate_dialogue_response(data, expected_count=2)
        assert result["recommendations"] == []

    def test_missing_style_dynamics(self):
        data = self._base_response()
        data["style_dynamics"] = 123
        result = _validate_dialogue_response(data, expected_count=2)
        assert result["style_dynamics"] == ""


# ─── _compute_target_vector ─────────────────────────────────────────────────


class TestComputeTargetVector:
    def test_default_shifts(self):
        user = {"warmth": 0, "formality": 0, "playfulness": 0, "assertiveness": 0, "expressiveness": 0}
        target = _compute_target_vector(user)
        assert target["warmth"] == 1      # +1
        assert target["formality"] == 1   # +1
        assert target["playfulness"] == 0 # toward 0 (already 0)
        assert target["assertiveness"] == -1  # -1
        assert target["expressiveness"] == -1 # -1

    def test_playfulness_shifted_toward_zero(self):
        user = {"warmth": 0, "formality": 0, "playfulness": 2, "assertiveness": 0, "expressiveness": 0}
        target = _compute_target_vector(user)
        assert target["playfulness"] == 1  # 2 → 1

        user["playfulness"] = -2
        target = _compute_target_vector(user)
        assert target["playfulness"] == -1  # -2 → -1

    def test_clamping(self):
        user = {"warmth": 2, "formality": 2, "playfulness": 0, "assertiveness": -2, "expressiveness": -2}
        target = _compute_target_vector(user)
        assert target["warmth"] == 2      # 2+1 clamped to 2
        assert target["formality"] == 2   # 2+1 clamped to 2
        assert target["assertiveness"] == -2  # -2-1 clamped to -2
        assert target["expressiveness"] == -2 # -2-1 clamped to -2

    def test_custom_shifts(self):
        user = {"warmth": 0, "formality": 0, "playfulness": 0, "assertiveness": 0, "expressiveness": 0}
        shifts = {"warmth": 2, "formality": -2, "playfulness": 0, "assertiveness": 0, "expressiveness": 0}
        target = _compute_target_vector(user, shifts=shifts)
        assert target["warmth"] == 2
        assert target["formality"] == -2


# ─── _parse_json_response ───────────────────────────────────────────────────


class TestParseJsonResponse:
    def test_bare_json(self):
        result = _parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_fences(self):
        raw = '```json\n{"key": "value"}\n```'
        result = _parse_json_response(raw)
        assert result == {"key": "value"}

    def test_fences_no_language(self):
        raw = '```\n{"key": "value"}\n```'
        result = _parse_json_response(raw)
        assert result == {"key": "value"}

    def test_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json_response("not json")

    def test_array_top_level_rejected(self):
        with pytest.raises(AssertionError, match="Expected JSON object"):
            _parse_json_response('[1, 2, 3]')

    def test_whitespace_stripped(self):
        result = _parse_json_response('  \n  {"a": 1}  \n  ')
        assert result == {"a": 1}


# ─── Markdown Formatters ────────────────────────────────────────────────────


class TestSvMd:
    def test_format(self):
        sv = {"warmth": 1, "formality": -1, "playfulness": 0, "assertiveness": 2, "expressiveness": -2}
        result = _sv_md(sv, "Test")
        assert result == "**Test:** W=+1 F=-1 P=+0 A=+2 E=-2"


class TestFormatAnalyze:
    def _data(self) -> dict:
        return {
            "emotion": "anger",
            "intensity": 2,
            "style_vector": {"warmth": -1, "formality": 0, "playfulness": 0, "assertiveness": 1, "expressiveness": 1},
            "detected_style": "aggressive",
            "explanation": "Very upset.",
            "triggers": ["terrible"],
        }

    def test_json_format(self):
        result = _format_analyze(self._data(), ResponseFormat.JSON)
        parsed = json.loads(result)
        assert parsed["emotion"] == "anger"

    def test_markdown_format(self):
        result = _format_analyze(self._data(), ResponseFormat.MARKDOWN)
        assert "## Emotional Analysis" in result
        assert "anger" in result
        assert "W=" in result
        assert "terrible" in result


class TestFormatDeEscalate:
    def _data(self) -> dict:
        sv = {"warmth": 1, "formality": 1, "playfulness": 0, "assertiveness": 0, "expressiveness": 0}
        return {
            "rewritten_text": "I understand.",
            "original_style_vector": sv,
            "result_style_vector": sv,
            "target_style_vector": sv,
            "user_style_vector": sv,
            "changes_applied": ["Added empathy"],
        }

    def test_json_format(self):
        result = _format_de_escalate(self._data(), ResponseFormat.JSON)
        parsed = json.loads(result)
        assert parsed["rewritten_text"] == "I understand."

    def test_markdown_format(self):
        result = _format_de_escalate(self._data(), ResponseFormat.MARKDOWN)
        assert "## De-escalated Response" in result
        assert "I understand." in result
        assert "Added empathy" in result


class TestFormatDialogue:
    def _data(self) -> dict:
        sv = {"warmth": 0, "formality": 0, "playfulness": 0, "assertiveness": 0, "expressiveness": 0}
        return {
            "message_analyses": [
                {"role": "user", "emotion": "anger", "style_vector": sv, "detected_style": "aggressive"},
            ],
            "overall_trend": "stable_neutral",
            "interaction_quality": "acceptable",
            "feedback_loop_risk": "low",
            "style_dynamics": "Stable.",
            "recommendations": ["Be kind"],
        }

    def test_json_format(self):
        result = _format_dialogue(self._data(), ResponseFormat.JSON)
        parsed = json.loads(result)
        assert parsed["overall_trend"] == "stable_neutral"

    def test_markdown_format(self):
        result = _format_dialogue(self._data(), ResponseFormat.MARKDOWN)
        assert "## Dialogue Dynamics" in result
        assert "| # | Role | Emotion" in result
        assert "Be kind" in result


# ─── Input Model Validation ─────────────────────────────────────────────────


class TestInputModels:
    def test_analyze_input_valid(self):
        inp = AnalyzeInput(text="Hello")
        assert inp.text == "Hello"

    def test_analyze_input_empty_text(self):
        with pytest.raises(ValidationError):
            AnalyzeInput(text="")

    def test_dialogue_message_valid(self):
        msg = DialogueMessage(role="user", text="Hi")
        assert msg.role == "user"

    def test_dialogue_message_invalid_role(self):
        with pytest.raises(ValidationError):
            DialogueMessage(role="admin", text="Hi")

    def test_evaluate_input_needs_user_message(self):
        with pytest.raises(ValidationError, match="at least one user"):
            EvaluateDialogueInput(messages=[
                DialogueMessage(role="bot", text="Hello"),
                DialogueMessage(role="bot", text="World"),
            ])

    def test_evaluate_input_min_messages(self):
        with pytest.raises(ValidationError):
            EvaluateDialogueInput(messages=[DialogueMessage(role="user", text="Hi")])

    def test_de_escalate_input_valid(self):
        inp = DeEscalateInput(user_message="I'm angry", draft_response="Sorry.")
        assert inp.preserve_facts is True


# ─── MCP Tool Functions (with mocked LLM) ───────────────────────────────────


@pytest.fixture
def mock_llm():
    """Patch _llm_call to return controlled responses."""
    with patch("server._llm_call", new_callable=AsyncMock) as m:
        yield m


class TestEmotionAnalyzeTool:
    @pytest.mark.asyncio
    async def test_success_json(self, mock_llm):
        mock_llm.return_value = json.dumps({
            "emotion": "anger",
            "intensity": 1,
            "style_vector": {"warmth": -1, "formality": 0, "playfulness": 0, "assertiveness": 1, "expressiveness": 1},
            "detected_style": "aggressive",
            "explanation": "Upset.",
            "triggers": ["terrible"],
        })
        params = AnalyzeInput(text="This is terrible!")
        result = await emotion_analyze(params)
        parsed = json.loads(result)
        assert parsed["emotion"] == "anger"
        assert parsed["style_vector"]["warmth"] == -1

    @pytest.mark.asyncio
    async def test_success_markdown(self, mock_llm):
        mock_llm.return_value = json.dumps({
            "emotion": "happiness",
            "intensity": 1,
            "style_vector": {"warmth": 1, "formality": 0, "playfulness": 1, "assertiveness": 0, "expressiveness": 1},
            "detected_style": "friendly",
            "explanation": "Positive.",
            "triggers": [],
        })
        params = AnalyzeInput(text="Great job!", response_format=ResponseFormat.MARKDOWN)
        result = await emotion_analyze(params)
        assert "## Emotional Analysis" in result

    @pytest.mark.asyncio
    async def test_malformed_json_returns_error(self, mock_llm):
        mock_llm.return_value = "not json at all"
        params = AnalyzeInput(text="Hello")
        result = await emotion_analyze(params)
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_llm_exception_returns_error(self, mock_llm):
        mock_llm.side_effect = RuntimeError("API down")
        params = AnalyzeInput(text="Hello")
        result = await emotion_analyze(params)
        parsed = json.loads(result)
        assert "error" in parsed
        assert "RuntimeError" in parsed["error"]

    @pytest.mark.asyncio
    async def test_with_context_and_language(self, mock_llm):
        mock_llm.return_value = json.dumps({
            "emotion": "neutral",
            "intensity": 0,
            "style_vector": {"warmth": 0, "formality": 0, "playfulness": 0, "assertiveness": 0, "expressiveness": 0},
            "detected_style": "neutral",
            "explanation": "",
            "triggers": [],
        })
        params = AnalyzeInput(text="OK", context="Previous chat", language_hint="en")
        result = await emotion_analyze(params)
        # Verify context and language hint were passed to LLM
        call_args = mock_llm.call_args[0][1]
        assert "Previous chat" in call_args
        assert "Language: en" in call_args


class TestEmotionDeEscalateTool:
    @pytest.mark.asyncio
    async def test_auto_mode(self, mock_llm):
        # First call: analyze user message; second call: de-escalate
        analyze_resp = json.dumps({
            "emotion": "anger",
            "intensity": 2,
            "style_vector": {"warmth": -1, "formality": -1, "playfulness": 0, "assertiveness": 2, "expressiveness": 2},
            "detected_style": "aggressive",
            "explanation": "",
            "triggers": [],
        })
        de_escalate_resp = json.dumps({
            "rewritten_text": "I understand your frustration.",
            "original_style_vector": {"warmth": -1, "formality": -1, "playfulness": 0, "assertiveness": 2, "expressiveness": 2},
            "result_style_vector": {"warmth": 1, "formality": 1, "playfulness": 0, "assertiveness": 0, "expressiveness": 0},
            "changes_applied": ["Softened tone"],
        })
        mock_llm.side_effect = [analyze_resp, de_escalate_resp]

        params = DeEscalateInput(
            user_message="This is unacceptable!",
            draft_response="Deal with it.",
        )
        result = await emotion_de_escalate(params)
        parsed = json.loads(result)
        assert parsed["rewritten_text"] == "I understand your frustration."
        assert "user_style_vector" in parsed
        assert "target_style_vector" in parsed

    @pytest.mark.asyncio
    async def test_explicit_target(self, mock_llm):
        de_escalate_resp = json.dumps({
            "rewritten_text": "Custom rewrite.",
            "original_style_vector": {"warmth": 0, "formality": 0, "playfulness": 0, "assertiveness": 0, "expressiveness": 0},
            "result_style_vector": {"warmth": 2, "formality": 2, "playfulness": 0, "assertiveness": -2, "expressiveness": -2},
            "changes_applied": ["Applied custom target"],
        })
        mock_llm.return_value = de_escalate_resp

        params = DeEscalateInput(
            user_message="I'm upset",
            draft_response="Sorry.",
            target_style=StyleVector(warmth=2, formality=2, assertiveness=-2, expressiveness=-2),
        )
        result = await emotion_de_escalate(params)
        parsed = json.loads(result)
        assert parsed["rewritten_text"] == "Custom rewrite."
        # Should NOT have user_style_vector when explicit target is used
        assert "user_style_vector" not in parsed
        # Only one LLM call (no analyze call)
        assert mock_llm.call_count == 1

    @pytest.mark.asyncio
    async def test_analyze_failure_uses_zero_vector(self, mock_llm):
        # First call (analyze) fails, second call (de-escalate) succeeds
        de_escalate_resp = json.dumps({
            "rewritten_text": "Fallback rewrite.",
            "original_style_vector": {"warmth": 0, "formality": 0, "playfulness": 0, "assertiveness": 0, "expressiveness": 0},
            "result_style_vector": {"warmth": 1, "formality": 1, "playfulness": 0, "assertiveness": -1, "expressiveness": -1},
            "changes_applied": [],
        })
        mock_llm.side_effect = [RuntimeError("analyze failed"), de_escalate_resp]

        params = DeEscalateInput(user_message="Hi", draft_response="Hello")
        result = await emotion_de_escalate(params)
        parsed = json.loads(result)
        assert parsed["rewritten_text"] == "Fallback rewrite."

    @pytest.mark.asyncio
    async def test_de_escalate_llm_failure(self, mock_llm):
        analyze_resp = json.dumps({
            "emotion": "neutral", "intensity": 0,
            "style_vector": {"warmth": 0, "formality": 0, "playfulness": 0, "assertiveness": 0, "expressiveness": 0},
            "detected_style": "neutral", "explanation": "", "triggers": [],
        })
        mock_llm.side_effect = [analyze_resp, RuntimeError("de-escalate failed")]

        params = DeEscalateInput(user_message="Hi", draft_response="Hello")
        result = await emotion_de_escalate(params)
        parsed = json.loads(result)
        assert "error" in parsed


class TestEmotionEvaluateDialogueTool:
    @pytest.mark.asyncio
    async def test_success(self, mock_llm):
        sv = {"warmth": 0, "formality": 0, "playfulness": 0, "assertiveness": 0, "expressiveness": 0}
        mock_llm.return_value = json.dumps({
            "message_analyses": [
                {"role": "user", "emotion": "anger", "style_vector": sv, "detected_style": "aggressive"},
                {"role": "bot", "emotion": "neutral", "style_vector": sv, "detected_style": "professional"},
            ],
            "overall_trend": "de_escalating",
            "interaction_quality": "good",
            "feedback_loop_risk": "none",
            "style_dynamics": "Improved.",
            "recommendations": ["Continue"],
        })

        params = EvaluateDialogueInput(messages=[
            DialogueMessage(role="user", text="I'm angry!"),
            DialogueMessage(role="bot", text="I understand."),
        ])
        result = await emotion_evaluate_dialogue(params)
        parsed = json.loads(result)
        assert parsed["overall_trend"] == "de_escalating"
        assert len(parsed["message_analyses"]) == 2

    @pytest.mark.asyncio
    async def test_llm_failure(self, mock_llm):
        mock_llm.side_effect = RuntimeError("API error")
        params = EvaluateDialogueInput(messages=[
            DialogueMessage(role="user", text="Hi"),
            DialogueMessage(role="bot", text="Hello"),
        ])
        result = await emotion_evaluate_dialogue(params)
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_markdown_output(self, mock_llm):
        sv = {"warmth": 0, "formality": 0, "playfulness": 0, "assertiveness": 0, "expressiveness": 0}
        mock_llm.return_value = json.dumps({
            "message_analyses": [
                {"role": "user", "emotion": "neutral", "style_vector": sv, "detected_style": "calm"},
                {"role": "bot", "emotion": "neutral", "style_vector": sv, "detected_style": "professional"},
            ],
            "overall_trend": "stable_neutral",
            "interaction_quality": "good",
            "feedback_loop_risk": "none",
            "style_dynamics": "Stable.",
            "recommendations": [],
        })

        params = EvaluateDialogueInput(
            messages=[
                DialogueMessage(role="user", text="Hi"),
                DialogueMessage(role="bot", text="Hello"),
            ],
            response_format=ResponseFormat.MARKDOWN,
        )
        result = await emotion_evaluate_dialogue(params)
        assert "## Dialogue Dynamics" in result