"""Tests for spaCy NLP integration — lemmatization and content word extraction."""

from __future__ import annotations

import pytest

from src.nlp import lemma_set, content_word_set, contains_any_lemma, text_contains_substring
from src.models import DialogueMessage
from src.pattern_detector import (
    detect_legal_threat,
    detect_churn_signal,
    detect_human_request,
    detect_repeated_bot_questions,
)


def _msg(role: str, text: str) -> DialogueMessage:
    return DialogueMessage(role=role, text=text)


# ─── Lemmatization ───────────────────────────────────────────────────────────


class TestLemmaSet:
    def test_russian_noun_forms(self):
        """Different case forms of a noun should produce the same lemma."""
        lemmas = lemma_set("жалобу жалобы жалоба")
        assert "жалоба" in lemmas

    def test_russian_verb_forms(self):
        """Different verb forms should share a lemma."""
        lemmas = lemma_set("требую требовать потребовал")
        assert "требовать" in lemmas

    def test_filters_short_tokens(self):
        """Tokens shorter than 2 chars should be excluded."""
        lemmas = lemma_set("я в к на у до за")
        # Most single-char prepositions should be filtered
        assert all(len(l) >= 2 for l in lemmas)

    def test_english_text(self):
        lemmas = lemma_set("lawyers lawsuits suing")
        # spaCy should handle basic English even with ru model loaded
        # May not perfectly lemmatize English but should still work
        assert len(lemmas) > 0


class TestContentWordSet:
    def test_extracts_nouns_from_question(self):
        """Should extract the object of a question, not the verb."""
        content = content_word_set("Уточните номер заказа для проверки")
        # Should contain the nouns: номер, заказ
        assert "номер" in content or "заказ" in content

    def test_similar_questions_share_content(self):
        """Different phrasing of the same question should have overlapping content."""
        q1 = content_word_set("Уточните номер заказа.")
        q2 = content_word_set("Подскажите номер заказа для проверки.")
        q3 = content_word_set("Напишите номер заказа, чтобы я мог помочь.")
        # All three ask about номер заказа
        common = q1 & q2 & q3
        assert len(common) > 0

    def test_different_questions_differ(self):
        """Questions about different things should have little overlap."""
        q1 = content_word_set("Укажите номер заказа.")
        q2 = content_word_set("С какого адреса была оформлена доставка?")
        # Different objects
        common = q1 & q2
        # May have some overlap but should be small relative to union
        if q1 | q2:
            similarity = len(common) / len(q1 | q2)
            assert similarity < 0.5


class TestContainsAnyLemma:
    def test_matches_inflected_form(self):
        """'судом' should match keyword lemma 'суд'."""
        keywords = {"суд", "жалоба", "адвокат"}
        found = contains_any_lemma("Обращусь в суд!", keywords)
        assert "суд" in found

    def test_matches_verb_form(self):
        """'требую' should match keyword lemma 'требовать'."""
        keywords = {"требовать", "обман"}
        found = contains_any_lemma("Я требую ответа!", keywords)
        assert "требовать" in found

    def test_no_false_positive_on_unrelated(self):
        """Should not match unrelated text."""
        keywords = {"суд", "адвокат", "иск"}
        found = contains_any_lemma("Когда придёт мой заказ?", keywords)
        assert len(found) == 0

    def test_подписку_does_not_match_иск(self):
        """'подписку' should NOT match 'иск' — different lemma."""
        keywords = {"иск"}
        found = contains_any_lemma("Отменяю подписку!", keywords)
        assert "иск" not in found


# ─── Improved Detection with Lemmatization ───────────────────────────────────


class TestLegalThreatWithLemmas:
    def test_detects_inflected_forms(self):
        """Should detect legal threat in various grammatical forms."""
        # "жалобу" → lemma "жалоба"
        msgs = [_msg("user", "Напишу жалобу в прокуратуру!")]
        result = detect_legal_threat(msgs)
        assert result is not None
        assert result.pattern_type == "legal_threat"

    def test_detects_verb_form(self):
        """'судить' should trigger via lemma."""
        msgs = [_msg("user", "Буду судиться с вами!")]
        result = detect_legal_threat(msgs)
        assert result is not None

    def test_no_false_positive_подписку(self):
        """'подписку' should not trigger legal threat."""
        msgs = [_msg("user", "Отменяю подписку!")]
        result = detect_legal_threat(msgs)
        assert result is None


class TestChurnWithLemmas:
    def test_detects_verb_forms(self):
        """Various verb forms of 'отказаться' should trigger churn."""
        msgs = [_msg("user", "Я отказываюсь от ваших услуг!")]
        result = detect_churn_signal(msgs)
        assert result is not None

    def test_detects_multi_word_phrase(self):
        """Multi-word phrases should be caught by substring fallback."""
        msgs = [_msg("user", "Верните деньги немедленно!")]
        result = detect_churn_signal(msgs)
        assert result is not None


class TestHumanRequestWithLemmas:
    def test_detects_inflected_form(self):
        """'руководителю' → lemma 'руководитель'."""
        msgs = [_msg("user", "Мне нужно поговорить с руководителем!")]
        result = detect_human_request(msgs)
        assert result is not None

    def test_detects_verb_переведите(self):
        """'переведите' → lemma 'перевести'."""
        msgs = [_msg("user", "Переведите на живого человека!")]
        result = detect_human_request(msgs)
        assert result is not None


class TestRepeatedQuestionsWithSpacy:
    def test_catches_rephrased_questions(self):
        """Questions with different verbs but same object should be detected."""
        messages = [
            _msg("user", "Где заказ?"),
            _msg("bot", "Уточните номер заказа."),
            _msg("user", "Не знаю!"),
            _msg("bot", "Подскажите номер заказа для проверки."),
            _msg("user", "Хватит!"),
            _msg("bot", "Назовите номер заказа, пожалуйста."),
        ]
        result = detect_repeated_bot_questions(messages)
        assert result is not None
        assert result.details["count"] >= 2
