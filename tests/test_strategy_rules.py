"""Tests for strategy rule engine."""

from __future__ import annotations

import pytest

from src.models import DetectedPattern, DialogueMessage, UserMetadata
from src.strategy_rules import suggest_strategy


def _msg(role: str, text: str) -> DialogueMessage:
    return DialogueMessage(role=role, text=text)


# ─── Scenario: Repeated order number request ─────────────────────────────────


class TestRepeatedQuestionStrategy:
    def test_recommends_alternative_identification(self):
        messages = [
            _msg("user", "Где мой заказ?"),
            _msg("bot", "Уточните номер заказа."),
            _msg("user", "Не знаю номер!"),
            _msg("bot", "Напишите номер заказа."),
            _msg("user", "Сделайте что-нибудь!"),
        ]
        patterns = [
            DetectedPattern(
                pattern_type="repeated_question",
                severity="critical",
                confidence=0.9,
                evidence=["Bot asked 3 times"],
                details={"count": 3},
            ),
        ]
        result = suggest_strategy(
            messages, patterns,
            available_actions=["request_order_number", "lookup_by_phone", "escalate_to_human"],
            language="ru",
        )
        assert result.recommended_strategy == "alternative_identification"
        actions = [a.action for a in result.action_sequence]
        assert "acknowledge_repetition" in actions
        assert "lookup_by_phone" in actions
        assert "escalate_to_human" in actions

    def test_anti_patterns_include_repeated_question(self):
        messages = [
            _msg("user", "Где мой заказ?"),
            _msg("bot", "Уточните номер заказа."),
            _msg("user", "Не знаю!"),
            _msg("bot", "Напишите номер заказа."),
        ]
        patterns = [
            DetectedPattern(
                pattern_type="repeated_question",
                severity="warning",
                confidence=0.8,
                details={"count": 2},
            ),
        ]
        result = suggest_strategy(messages, patterns, language="ru")
        assert any("НЕ спрашивать" in ap for ap in result.anti_patterns)

    def test_escalate_now_after_4_repetitions(self):
        patterns = [
            DetectedPattern(
                pattern_type="repeated_question",
                severity="critical",
                confidence=0.95,
                details={"count": 4},
            ),
        ]
        result = suggest_strategy([_msg("user", "!!!")], patterns, language="ru")
        assert result.escalation.should_escalate_now is True


# ─── Scenario: Legal threat ──────────────────────────────────────────────────


class TestLegalThreatStrategy:
    def test_immediate_escalation(self):
        messages = [_msg("user", "Пишу заявление в суд!")]
        patterns = [
            DetectedPattern(
                pattern_type="legal_threat",
                severity="critical",
                confidence=0.9,
                details={"keywords_found": ["суд"]},
            ),
        ]
        result = suggest_strategy(
            messages, patterns,
            available_actions=["escalate_to_human", "escalate_to_supervisor"],
            language="ru",
        )
        assert result.recommended_strategy == "immediate_supervisor_escalation"
        assert result.escalation.should_escalate_now is True
        actions = [a.action for a in result.action_sequence]
        assert "escalate_to_supervisor" in actions

    def test_anti_patterns_no_legal_advice(self):
        messages = [_msg("user", "Подам иск!")]
        patterns = [
            DetectedPattern(
                pattern_type="legal_threat",
                severity="critical",
                confidence=0.9,
                details={"keywords_found": ["иск"]},
            ),
        ]
        result = suggest_strategy(messages, patterns, language="ru")
        assert any("юридических" in ap for ap in result.anti_patterns)


# ─── Scenario: Human request ─────────────────────────────────────────────────


class TestHumanRequestStrategy:
    def test_comply_immediately(self):
        messages = [_msg("user", "Переведите на оператора!")]
        patterns = [
            DetectedPattern(
                pattern_type="human_request",
                severity="critical",
                confidence=0.95,
                details={"keywords_found": ["оператор"]},
            ),
        ]
        result = suggest_strategy(
            messages, patterns,
            available_actions=["escalate_to_human"],
            language="ru",
        )
        assert result.recommended_strategy == "comply_with_human_request"
        assert result.escalation.should_escalate_now is True


# ─── Scenario: Churn signal ──────────────────────────────────────────────────


class TestChurnStrategy:
    def test_retention_strategy(self):
        messages = [_msg("user", "Всё, отменяю, ухожу к конкурентам!")]
        patterns = [
            DetectedPattern(
                pattern_type="churn_signal",
                severity="critical",
                confidence=0.8,
                details={"keywords_found": ["отмен", "конкурент"]},
            ),
        ]
        result = suggest_strategy(
            messages, patterns,
            available_actions=["provide_compensation", "escalate_to_human"],
            language="ru",
        )
        assert result.recommended_strategy == "retention"
        actions = [a.action for a in result.action_sequence]
        assert "provide_compensation" in actions


# ─── Scenario: No patterns ───────────────────────────────────────────────────


class TestDefaultStrategy:
    def test_continue_normally(self):
        messages = [
            _msg("user", "Добрый день"),
            _msg("bot", "Здравствуйте! Чем могу помочь?"),
        ]
        result = suggest_strategy(messages, patterns=[], language="ru")
        assert result.recommended_strategy == "continue_normally"
        assert result.escalation.should_escalate_now is False


# ─── Scenario: Multiple patterns (priority) ──────────────────────────────────


class TestPatternPriority:
    def test_legal_threat_takes_priority(self):
        """Legal threat should override repeated question."""
        messages = [_msg("user", "Подам в суд, надоели!")]
        patterns = [
            DetectedPattern(
                pattern_type="repeated_question",
                severity="warning",
                confidence=0.7,
                details={"count": 2},
            ),
            DetectedPattern(
                pattern_type="legal_threat",
                severity="critical",
                confidence=0.9,
                details={"keywords_found": ["суд"]},
            ),
        ]
        result = suggest_strategy(messages, patterns, language="ru")
        assert result.recommended_strategy == "immediate_supervisor_escalation"
        # All patterns should be included
        assert len(result.detected_patterns) == 2

    def test_human_request_before_churn(self):
        messages = [_msg("user", "Оператора! Отменяю всё!")]
        patterns = [
            DetectedPattern(
                pattern_type="churn_signal",
                severity="warning",
                confidence=0.7,
                details={"keywords_found": ["отмен"]},
            ),
            DetectedPattern(
                pattern_type="human_request",
                severity="critical",
                confidence=0.95,
                details={"keywords_found": ["оператор"]},
            ),
        ]
        result = suggest_strategy(messages, patterns, language="ru")
        assert result.recommended_strategy == "comply_with_human_request"


# ─── Phrase tracking in anti-patterns ─────────────────────────────────────────


class TestPhraseTracking:
    def test_overused_empathy_phrase_in_anti_patterns(self):
        messages = [
            _msg("user", "Плохо!"),
            _msg("bot", "Понимаю ваше раздражение. Уточните номер."),
            _msg("user", "Ужасно!"),
            _msg("bot", "Понимаю ваше раздражение. Напишите номер."),
            _msg("user", "Хватит!"),
        ]
        patterns = [
            DetectedPattern(
                pattern_type="emotion_escalation",
                severity="warning",
                confidence=0.7,
                details={"direction": "increasing"},
            ),
        ]
        result = suggest_strategy(messages, patterns, language="ru")
        assert any("понимаю ваш" in ap.lower() for ap in result.anti_patterns)


# ─── English language ─────────────────────────────────────────────────────────


class TestEnglishLanguage:
    def test_english_strategy(self):
        messages = [_msg("user", "Where is my order?")]
        patterns = [
            DetectedPattern(
                pattern_type="repeated_question",
                severity="warning",
                confidence=0.7,
                details={"count": 2},
            ),
        ]
        result = suggest_strategy(
            messages, patterns,
            available_actions=["lookup_by_phone"],
            language="en",
        )
        assert result.recommended_strategy == "alternative_identification"
        # Notes should be in English
        for action in result.action_sequence:
            if action.note:
                assert "Acknowledge" in action.note or "Offer" in action.note or "If" in action.note
