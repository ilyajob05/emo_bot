"""
Deterministic pattern detector for support dialogues.

Detects problematic patterns (repeated questions, escalation, legal threats, etc.)
without any LLM calls. Uses spaCy for lemmatization and POS-based content extraction.
Works offline, is fast, and produces predictable results.
"""

from __future__ import annotations

import re

from .models import DetectedPattern, DialogueMessage
from .nlp import lemma_set, content_word_set, contains_any_lemma, text_contains_substring


# ─── Similarity ──────────────────────────────────────────────────────────────

def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ─── Keyword Dictionaries (lemma form) ───────────────────────────────────────
# Keywords are now stored as LEMMA SETS for matching via spaCy lemmatization.
# Multi-word phrases are matched via substring fallback.

# Legal/regulatory — lemmas
LEGAL_LEMMAS_RU: set[str] = {
    "суд", "судить", "судиться", "судебный",
    "жалоба",
    "прокуратура", "адвокат", "иск",
    "юрист", "претензия", "закон",
}
# Multi-word / proper nouns — substring matching (spaCy may not lemmatize these well)
LEGAL_SUBSTRINGS_RU: list[str] = [
    "роспотребнадзор", "подам иск", "подать иск", "подаю иск",
]
LEGAL_LEMMAS_EN: set[str] = {
    "lawsuit", "lawyer", "attorney", "sue",
    "court", "complaint", "regulatory",
}
LEGAL_SUBSTRINGS_EN: list[str] = [
    "legal action", "consumer protection", "attorney general",
]

# Churn — lemmas
CHURN_LEMMAS_RU: set[str] = {
    "отказ", "отказаться", "отказываться", "отмена", "отменить", "отменять",
    "уйти", "уходить", "перейти",
    "конкурент", "возврат",
}
CHURN_SUBSTRINGS_RU: list[str] = [
    "другой сервис", "другую компанию",
    "больше не буду", "не буду пользоваться",
    "закрыть аккаунт", "удалить аккаунт",
    "верните деньги",
]
CHURN_LEMMAS_EN: set[str] = {
    "cancel", "refund", "competitor", "leave", "unsubscribe",
}
CHURN_SUBSTRINGS_EN: list[str] = [
    "close my account", "delete my account",
    "switch to", "done with you", "never again",
]

# Human request — lemmas
HUMAN_LEMMAS_RU: set[str] = {
    "оператор", "человек", "менеджер",
    "руководитель", "начальник", "старший", "супервайзер",
    "перевести", "переключить", "соединить",
}
HUMAN_SUBSTRINGS_RU: list[str] = [
    "живой человек",
]
HUMAN_LEMMAS_EN: set[str] = {
    "supervisor", "representative", "operator", "agent",
}
HUMAN_SUBSTRINGS_EN: list[str] = [
    "speak to a manager", "real person", "human agent",
    "transfer me", "connect me", "real human", "talk to someone",
]

# Escalation markers — lemmas
ESCALATION_LEMMAS_RU: set[str] = {
    "немедленно", "требовать", "безобразие",
    "кошмар", "ужас", "позор", "хватить", "доставать",
    "обман", "мошенничество", "наглость", "хамство",
}
ESCALATION_SUBSTRINGS_RU: list[str] = [
    "сейчас же",
]
ESCALATION_LEMMAS_EN: set[str] = {
    "immediately", "unacceptable", "demand",
    "ridiculous", "outrageous", "disgusting", "pathetic",
    "fraud", "scam", "incompetent", "worst",
}
ESCALATION_SUBSTRINGS_EN: list[str] = [
    "right now",
]


def _find_keywords(
    text: str,
    lemmas_ru: set[str],
    lemmas_en: set[str],
    substrings_ru: list[str] | None = None,
    substrings_en: list[str] | None = None,
) -> list[str]:
    """Find keywords in text using lemma matching + substring fallback.

    Returns list of matched keywords (lemmas or substrings).
    """
    found: list[str] = []

    # Lemma-based matching (handles morphology automatically)
    matched_lemmas = contains_any_lemma(text, lemmas_ru | lemmas_en)
    found.extend(sorted(matched_lemmas))

    # Substring fallback for multi-word phrases and proper nouns
    if substrings_ru:
        found.extend(text_contains_substring(text, substrings_ru))
    if substrings_en:
        found.extend(text_contains_substring(text, substrings_en))

    return found


# ─── Individual Pattern Detectors ─────────────────────────────────────────────

# Regex to identify question-like bot messages
_QUESTION_RE = re.compile(
    r"[?？]|уточн|напиш|укаж|подскаж|сообщ|пришл|назов"
    r"|provide|specify|send|tell me|what is|could you",
    re.IGNORECASE,
)


def detect_repeated_bot_questions(
    messages: list[DialogueMessage],
    similarity_threshold: float = 0.45,
) -> DetectedPattern | None:
    """Detect when the bot asks the same (or very similar) question repeatedly.

    Uses spaCy POS-based content word extraction to compare what the bot
    is asking for (nouns), ignoring how it asks (verbs).
    """
    bot_messages = [
        (i, m.text) for i, m in enumerate(messages) if m.role == "bot"
    ]
    if len(bot_messages) < 2:
        return None

    bot_questions: list[tuple[int, str]] = []
    for idx, text in bot_messages:
        if _QUESTION_RE.search(text):
            bot_questions.append((idx, text))

    if len(bot_questions) < 2:
        return None

    # Compare content words (nouns via spaCy POS), not full text
    content_sets = [(idx, content_word_set(text)) for idx, text in bot_questions]

    # Find clusters of similar questions
    clusters: dict[int, list[int]] = {}
    for i, (idx_a, ws_a) in enumerate(content_sets):
        for j in range(i + 1, len(content_sets)):
            idx_b, ws_b = content_sets[j]
            if _jaccard(ws_a, ws_b) >= similarity_threshold:
                root = clusters.setdefault(i, [i])
                if j not in clusters:
                    root.append(j)
                    clusters[j] = root

    # Find the largest cluster
    seen: set[int] = set()
    max_cluster: list[int] = []
    for members in clusters.values():
        key = id(members)
        if key in seen:
            continue
        seen.add(key)
        if len(members) > len(max_cluster):
            max_cluster = members

    if len(max_cluster) < 2:
        return None

    count = len(max_cluster)
    sample_texts = [bot_questions[i][1][:80] for i in max_cluster[:3]]

    return DetectedPattern(
        pattern_type="repeated_question",
        severity="critical" if count >= 3 else "warning",
        confidence=min(0.5 + count * 0.15, 1.0),
        evidence=[f"Bot asked similar question {count} times"] + sample_texts,
        details={"count": count, "message_indices": [bot_questions[i][0] for i in max_cluster]},
    )


def detect_no_progress(
    messages: list[DialogueMessage],
    window: int = 6,
) -> DetectedPattern | None:
    """Detect when the dialogue has no new information exchange.

    Looks at the last `window` messages: if bot keeps asking and user
    keeps complaining without providing new data, that's stagnation.
    """
    if len(messages) < 4:
        return None

    recent = messages[-window:]
    user_msgs = [m for m in recent if m.role == "user"]
    bot_msgs = [m for m in recent if m.role == "bot"]

    if len(user_msgs) < 2 or len(bot_msgs) < 2:
        return None

    # User messages: lemma-based similarity
    user_lemma_sets = [lemma_set(m.text) for m in user_msgs]
    overlaps = []
    for i in range(1, len(user_lemma_sets)):
        overlaps.append(_jaccard(user_lemma_sets[i - 1], user_lemma_sets[i]))
    avg_overlap = sum(overlaps) / len(overlaps) if overlaps else 0

    # Bot messages: content word (noun) similarity
    bot_content_sets = [content_word_set(m.text) for m in bot_msgs]
    bot_overlaps = []
    for i in range(1, len(bot_content_sets)):
        bot_overlaps.append(_jaccard(bot_content_sets[i - 1], bot_content_sets[i]))
    avg_bot_overlap = sum(bot_overlaps) / len(bot_overlaps) if bot_overlaps else 0

    # Both sides repeating themselves = no progress
    if avg_overlap > 0.3 and avg_bot_overlap > 0.15:
        turns = len(recent) // 2
        return DetectedPattern(
            pattern_type="no_progress",
            severity="critical" if turns >= 3 else "warning",
            confidence=min(0.4 + (avg_overlap + avg_bot_overlap) * 0.3, 1.0),
            evidence=[
                f"{turns} turn pairs without progress",
                f"User message similarity: {avg_overlap:.0%}",
                f"Bot message similarity: {avg_bot_overlap:.0%}",
            ],
            details={"turns_without_progress": turns},
        )

    return None


def detect_emotion_escalation(
    messages: list[DialogueMessage],
) -> DetectedPattern | None:
    """Detect escalating user emotion based on textual signals.

    Signals: increasing caps ratio, more exclamation marks,
    appearance of escalation marker words (via lemma matching).
    """
    user_msgs = [m for m in messages if m.role == "user"]
    if len(user_msgs) < 2:
        return None

    def _intensity_score(text: str) -> float:
        score = 0.0
        # Caps ratio (excluding short words)
        words = text.split()
        if words:
            upper_words = sum(1 for w in words if len(w) > 2 and w.isupper())
            score += (upper_words / len(words)) * 2

        # Exclamation density
        if len(text) > 0:
            score += min(text.count("!") / max(len(text) / 20, 1), 2.0)

        # Escalation keywords (lemma-based)
        found = _find_keywords(
            text,
            ESCALATION_LEMMAS_RU, ESCALATION_LEMMAS_EN,
            ESCALATION_SUBSTRINGS_RU, ESCALATION_SUBSTRINGS_EN,
        )
        score += len(found) * 0.5

        return score

    scores = [_intensity_score(m.text) for m in user_msgs]

    mid = len(scores) // 2
    if mid == 0:
        mid = 1
    first_half = sum(scores[:mid]) / mid
    second_half = sum(scores[mid:]) / len(scores[mid:])

    if second_half > first_half + 0.5:
        return DetectedPattern(
            pattern_type="emotion_escalation",
            severity="warning" if second_half < 3.0 else "critical",
            confidence=min(0.5 + (second_half - first_half) * 0.15, 1.0),
            evidence=[
                f"Intensity trend: {first_half:.1f} → {second_half:.1f}",
            ],
            details={
                "direction": "increasing",
                "first_half_score": round(first_half, 2),
                "second_half_score": round(second_half, 2),
            },
        )

    return None


def detect_legal_threat(
    messages: list[DialogueMessage],
) -> DetectedPattern | None:
    """Detect legal/regulatory threats in user messages."""
    user_msgs = [m for m in messages if m.role == "user"]
    if not user_msgs:
        return None

    recent_user = user_msgs[-3:]
    all_found: list[str] = []

    for m in recent_user:
        found = _find_keywords(
            m.text,
            LEGAL_LEMMAS_RU, LEGAL_LEMMAS_EN,
            LEGAL_SUBSTRINGS_RU, LEGAL_SUBSTRINGS_EN,
        )
        all_found.extend(found)

    if not all_found:
        return None

    unique_found = list(set(all_found))
    return DetectedPattern(
        pattern_type="legal_threat",
        severity="critical",
        confidence=min(0.7 + len(unique_found) * 0.1, 1.0),
        evidence=[f"Legal keywords found: {', '.join(unique_found)}"],
        details={"keywords_found": unique_found},
    )


def detect_churn_signal(
    messages: list[DialogueMessage],
) -> DetectedPattern | None:
    """Detect signals that the user is about to leave/cancel."""
    user_msgs = [m for m in messages if m.role == "user"]
    if not user_msgs:
        return None

    recent_user = user_msgs[-3:]
    all_found: list[str] = []

    for m in recent_user:
        found = _find_keywords(
            m.text,
            CHURN_LEMMAS_RU, CHURN_LEMMAS_EN,
            CHURN_SUBSTRINGS_RU, CHURN_SUBSTRINGS_EN,
        )
        all_found.extend(found)

    if not all_found:
        return None

    unique_found = list(set(all_found))
    return DetectedPattern(
        pattern_type="churn_signal",
        severity="critical" if len(unique_found) >= 2 else "warning",
        confidence=min(0.6 + len(unique_found) * 0.1, 1.0),
        evidence=[f"Churn signals: {', '.join(unique_found)}"],
        details={"keywords_found": unique_found},
    )


def detect_human_request(
    messages: list[DialogueMessage],
) -> DetectedPattern | None:
    """Detect explicit requests to speak with a human agent."""
    user_msgs = [m for m in messages if m.role == "user"]
    if not user_msgs:
        return None

    recent_user = user_msgs[-2:]
    all_found: list[str] = []

    for m in recent_user:
        found = _find_keywords(
            m.text,
            HUMAN_LEMMAS_RU, HUMAN_LEMMAS_EN,
            HUMAN_SUBSTRINGS_RU, HUMAN_SUBSTRINGS_EN,
        )
        all_found.extend(found)

    if not all_found:
        return None

    unique_found = list(set(all_found))
    return DetectedPattern(
        pattern_type="human_request",
        severity="critical",
        confidence=0.95,
        evidence=[f"User requested human: {', '.join(unique_found)}"],
        details={"keywords_found": unique_found},
    )


def detect_repeated_contact(
    messages: list[DialogueMessage],
    contacts_today: int = 1,
) -> DetectedPattern | None:
    """Detect when user has contacted support multiple times today."""
    if contacts_today < 2:
        return None

    return DetectedPattern(
        pattern_type="repeated_contact",
        severity="critical" if contacts_today >= 3 else "warning",
        confidence=1.0,
        evidence=[f"User contacted support {contacts_today} times today"],
        details={"contacts_today": contacts_today},
    )


# ─── Main Entry Point ────────────────────────────────────────────────────────

def detect_all_patterns(
    messages: list[DialogueMessage],
    contacts_today: int = 1,
) -> list[DetectedPattern]:
    """Run all pattern detectors and return found patterns, sorted by severity."""
    detectors = [
        lambda: detect_repeated_bot_questions(messages),
        lambda: detect_no_progress(messages),
        lambda: detect_emotion_escalation(messages),
        lambda: detect_legal_threat(messages),
        lambda: detect_churn_signal(messages),
        lambda: detect_human_request(messages),
        lambda: detect_repeated_contact(messages, contacts_today),
    ]

    patterns: list[DetectedPattern] = []
    for detector in detectors:
        result = detector()
        if result is not None:
            patterns.append(result)

    severity_order = {"critical": 0, "warning": 1, "info": 2}
    patterns.sort(key=lambda p: severity_order.get(p.severity, 99))

    return patterns
