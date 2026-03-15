"""Tests for deterministic pattern detector."""

from __future__ import annotations

import pytest

from src.models import DialogueMessage
from src.pattern_detector import (
    detect_all_patterns,
    detect_repeated_bot_questions,
    detect_no_progress,
    detect_emotion_escalation,
    detect_legal_threat,
    detect_churn_signal,
    detect_human_request,
    detect_repeated_contact,
)


def _msg(role: str, text: str) -> DialogueMessage:
    return DialogueMessage(role=role, text=text)


# ─── Repeated Bot Questions ──────────────────────────────────────────────────


class TestRepeatedBotQuestions:
    def test_detects_repeated_order_number_request(self):
        messages = [
            _msg("user", "Где мой заказ?"),
            _msg("bot", "Уточните номер заказа, пожалуйста."),
            _msg("user", "Я уже писал!"),
            _msg("bot", "Подскажите номер заказа для проверки."),
            _msg("user", "Доставьте уже!"),
            _msg("bot", "Напишите номер заказа, чтобы я мог помочь."),
        ]
        result = detect_repeated_bot_questions(messages)
        assert result is not None
        assert result.pattern_type == "repeated_question"
        assert result.severity == "critical"  # 3 times
        assert result.details["count"] >= 3

    def test_no_detection_with_different_questions(self):
        messages = [
            _msg("user", "Где мой заказ?"),
            _msg("bot", "Уточните номер заказа."),
            _msg("user", "123"),
            _msg("bot", "С какого адреса была оформлена доставка?"),
        ]
        result = detect_repeated_bot_questions(messages)
        assert result is None

    def test_two_repetitions_is_warning(self):
        messages = [
            _msg("user", "Где заказ?"),
            _msg("bot", "Укажите номер заказа."),
            _msg("user", "Не знаю номер"),
            _msg("bot", "Напишите номер заказа, пожалуйста."),
        ]
        result = detect_repeated_bot_questions(messages)
        assert result is not None
        assert result.severity == "warning"

    def test_single_bot_message_no_detection(self):
        messages = [
            _msg("user", "Привет"),
            _msg("bot", "Чем могу помочь?"),
        ]
        result = detect_repeated_bot_questions(messages)
        assert result is None


# ─── No Progress ─────────────────────────────────────────────────────────────


class TestNoProgress:
    def test_detects_stuck_dialogue(self):
        messages = [
            _msg("user", "Где мой заказ? Доставьте!"),
            _msg("bot", "Уточните номер заказа для проверки статуса."),
            _msg("user", "Где мой заказ?! Доставьте уже!"),
            _msg("bot", "Напишите номер заказа, чтобы проверить статус."),
            _msg("user", "Заказ! Где?! Доставьте!!!"),
            _msg("bot", "Пожалуйста, укажите номер заказа для проверки."),
        ]
        result = detect_no_progress(messages)
        assert result is not None
        assert result.pattern_type == "no_progress"

    def test_no_detection_with_progress(self):
        messages = [
            _msg("user", "Где мой заказ?"),
            _msg("bot", "Уточните номер заказа."),
            _msg("user", "Номер 12345"),
            _msg("bot", "Заказ 12345 в пути, будет завтра."),
        ]
        result = detect_no_progress(messages)
        assert result is None

    def test_too_few_messages(self):
        messages = [
            _msg("user", "Привет"),
            _msg("bot", "Здравствуйте"),
        ]
        result = detect_no_progress(messages)
        assert result is None


# ─── Emotion Escalation ──────────────────────────────────────────────────────


class TestEmotionEscalation:
    def test_detects_escalation(self):
        messages = [
            _msg("user", "Где мой заказ?"),
            _msg("bot", "Уточните номер."),
            _msg("user", "Ну где заказ?!"),
            _msg("bot", "Напишите номер."),
            _msg("user", "ДА ЧТО ЗА БЕЗОБРАЗИЕ!!! НЕМЕДЛЕННО РЕШИТЕ!!!"),
        ]
        result = detect_emotion_escalation(messages)
        assert result is not None
        assert result.pattern_type == "emotion_escalation"
        assert result.details["direction"] == "increasing"

    def test_no_escalation_in_calm_dialogue(self):
        messages = [
            _msg("user", "Добрый день, подскажите статус заказа"),
            _msg("bot", "Уточните номер заказа."),
            _msg("user", "Номер 12345, спасибо"),
        ]
        result = detect_emotion_escalation(messages)
        assert result is None

    def test_single_user_message(self):
        messages = [
            _msg("user", "УЖАС!!!"),
        ]
        result = detect_emotion_escalation(messages)
        assert result is None


# ─── Legal Threat ─────────────────────────────────────────────────────────────


class TestLegalThreat:
    def test_detects_russian_legal_keywords(self):
        messages = [
            _msg("user", "Пишу заявление в суд, вы пожалеете!"),
        ]
        result = detect_legal_threat(messages)
        assert result is not None
        assert result.pattern_type == "legal_threat"
        assert result.severity == "critical"

    def test_detects_multiple_keywords(self):
        messages = [
            _msg("user", "Обращусь к адвокату и подам иск!"),
        ]
        result = detect_legal_threat(messages)
        assert result is not None
        assert len(result.details["keywords_found"]) >= 2

    def test_detects_english_legal_keywords(self):
        messages = [
            _msg("user", "I'm taking legal action against your company!"),
        ]
        result = detect_legal_threat(messages)
        assert result is not None

    def test_no_detection_without_keywords(self):
        messages = [
            _msg("user", "Я недоволен обслуживанием"),
        ]
        result = detect_legal_threat(messages)
        assert result is None

    def test_rospotrebnadzor(self):
        messages = [
            _msg("user", "Буду жаловаться в роспотребнадзор!"),
        ]
        result = detect_legal_threat(messages)
        assert result is not None


# ─── Churn Signal ─────────────────────────────────────────────────────────────


class TestChurnSignal:
    def test_detects_cancel_intent(self):
        messages = [
            _msg("user", "Всё, отменяю подписку, ухожу к конкурентам!"),
        ]
        result = detect_churn_signal(messages)
        assert result is not None
        assert result.pattern_type == "churn_signal"

    def test_detects_refund_request(self):
        messages = [
            _msg("user", "Верните деньги немедленно!"),
        ]
        result = detect_churn_signal(messages)
        assert result is not None

    def test_no_detection_without_signals(self):
        messages = [
            _msg("user", "Когда придёт мой заказ?"),
        ]
        result = detect_churn_signal(messages)
        assert result is None


# ─── Human Request ────────────────────────────────────────────────────────────


class TestHumanRequest:
    def test_detects_operator_request(self):
        messages = [
            _msg("user", "Переведите меня на оператора!"),
        ]
        result = detect_human_request(messages)
        assert result is not None
        assert result.pattern_type == "human_request"
        assert result.severity == "critical"

    def test_detects_manager_request(self):
        messages = [
            _msg("user", "Хочу говорить с руководителем"),
        ]
        result = detect_human_request(messages)
        assert result is not None

    def test_detects_english_request(self):
        messages = [
            _msg("user", "I want to speak to a manager"),
        ]
        result = detect_human_request(messages)
        assert result is not None

    def test_no_detection_without_request(self):
        messages = [
            _msg("user", "Помогите с заказом"),
        ]
        result = detect_human_request(messages)
        assert result is None


# ─── Repeated Contact ────────────────────────────────────────────────────────


class TestRepeatedContact:
    def test_detects_third_contact(self):
        messages = [_msg("user", "Опять я")]
        result = detect_repeated_contact(messages, contacts_today=3)
        assert result is not None
        assert result.severity == "critical"

    def test_second_contact_is_warning(self):
        messages = [_msg("user", "Снова я")]
        result = detect_repeated_contact(messages, contacts_today=2)
        assert result is not None
        assert result.severity == "warning"

    def test_first_contact_no_detection(self):
        messages = [_msg("user", "Привет")]
        result = detect_repeated_contact(messages, contacts_today=1)
        assert result is None


# ─── detect_all_patterns ─────────────────────────────────────────────────────


class TestDetectAllPatterns:
    def test_multiple_patterns_detected(self):
        messages = [
            _msg("user", "Где мой заказ?"),
            _msg("bot", "Уточните номер заказа."),
            _msg("user", "Я не знаю номер!"),
            _msg("bot", "Напишите номер заказа, пожалуйста."),
            _msg("user", "Пишу жалобу в суд! Переведите на оператора!"),
        ]
        patterns = detect_all_patterns(messages, contacts_today=3)
        types = {p.pattern_type for p in patterns}
        assert "legal_threat" in types
        assert "human_request" in types
        assert "repeated_contact" in types

    def test_sorted_by_severity(self):
        messages = [
            _msg("user", "Где мой заказ?"),
            _msg("bot", "Уточните номер заказа."),
            _msg("user", "Переведите на оператора!"),
        ]
        patterns = detect_all_patterns(messages)
        if len(patterns) >= 2:
            severities = [p.severity for p in patterns]
            severity_order = {"critical": 0, "warning": 1, "info": 2}
            orders = [severity_order[s] for s in severities]
            assert orders == sorted(orders)

    def test_empty_on_normal_dialogue(self):
        messages = [
            _msg("user", "Добрый день, подскажите статус заказа 12345"),
            _msg("bot", "Здравствуйте! Заказ 12345 в пути, ожидается завтра."),
        ]
        patterns = detect_all_patterns(messages)
        assert len(patterns) == 0
