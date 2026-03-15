"""
Deterministic pattern detector for support dialogues.

Detects problematic patterns (repeated questions, escalation, legal threats, etc.)
without any LLM calls. Uses spaCy for lemmatization and POS-based content extraction.
Works offline, is fast, and produces predictable results.

Keywords and thresholds are loaded from config/patterns.toml.
Override via PATTERNS_CONFIG environment variable.
"""

from __future__ import annotations

import re

from .models import DetectedPattern, DialogueMessage
from .nlp import lemma_set, content_word_set, contains_any_lemma, text_contains_substring
from .pattern_config import get_keywords, get_threshold, get_question_pattern, get_regex_patterns


# ─── Similarity ──────────────────────────────────────────────────────────────

def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ─── Keyword Dictionaries ───────────────────────────────────────────────────
# Loaded from config/patterns.toml (override via PATTERNS_CONFIG env var).
# Falls back to built-in defaults if config file not found.
# See config/patterns.toml for format and customization guide.

# Built-in defaults (used when config file is missing)
_BUILTIN_LEGAL = (
    {"суд", "судить", "судиться", "судебный", "жалоба", "прокуратура", "адвокат", "иск", "юрист", "претензия", "закон"},
    ["роспотребнадзор", "подам иск", "подать иск", "подаю иск"],
    {"lawsuit", "lawyer", "attorney", "sue", "court", "complaint", "regulatory"},
    ["legal action", "consumer protection", "attorney general"],
)
_BUILTIN_CHURN = (
    {"отказ", "отказаться", "отказываться", "отмена", "отменить", "отменять", "уйти", "уходить", "перейти", "конкурент", "возврат"},
    ["другой сервис", "другую компанию", "больше не буду", "не буду пользоваться", "закрыть аккаунт", "удалить аккаунт", "верните деньги"],
    {"cancel", "refund", "competitor", "leave", "unsubscribe"},
    ["close my account", "delete my account", "switch to", "done with you", "never again"],
)
_BUILTIN_HUMAN = (
    {"оператор", "человек", "менеджер", "руководитель", "начальник", "старший", "супервайзер", "перевести", "переключить", "соединить"},
    ["живой человек"],
    {"supervisor", "representative", "operator", "agent"},
    ["speak to a manager", "real person", "human agent", "transfer me", "connect me", "real human", "talk to someone"],
)
_BUILTIN_ESCALATION = (
    {"немедленно", "требовать", "безобразие", "кошмар", "ужас", "позор", "хватить", "доставать", "обман", "мошенничество", "наглость", "хамство"},
    ["сейчас же"],
    {"immediately", "unacceptable", "demand", "ridiculous", "outrageous", "disgusting", "pathetic", "fraud", "scam", "incompetent", "worst"},
    ["right now"],
)
_BUILTIN_PROFANITY = (
    {"дурак", "идиот", "дебил", "тупой", "кретин", "урод", "мудак", "сука"},
    ["бля", "пизд", "хуй", "нахуй", "ебан"],
    {"idiot", "moron", "stupid", "asshole", "bastard"},
    ["fuck", "shit", "bullshit", "wtf", "stfu"],
)
_BUILTIN_PROFANITY_REGEX = (
    [r'бл[яЯ*#@!._]+[дтДТ]', r'х[уУ*#@!._]+[йеёЙЕЁ]', r'п[иИ*#@!._]+зд'],
    [r'f[*#@!._]+[ck]+', r'sh[*#@!._]+t', r'a[*#@!._]+hole'],
)
_BUILTIN_PUBLICITY = (
    {"отзыв", "рейтинг", "репутация", "блогер"},
    ["напишу отзыв", "негативный отзыв", "напишу в соцсет"],
    {"review", "rating", "reputation", "viral"},
    ["leave a review", "negative review", "social media", "post on twitter"],
)
_BUILTIN_REPEATED_CONTACT = (
    {"повторно", "повторный", "опять", "снова"},
    ["уже обращался", "уже обращалась", "третий раз", "не первый раз", "до сих пор"],
    {"again", "repeatedly", "still"},
    ["already contacted", "already called", "not the first time", "still waiting"],
)
_BUILTIN_VULNERABILITY = (
    {"инвалид", "пенсионер", "болезнь", "беременность", "пособие", "ветеран"},
    ["мать одиночка", "нет денег", "тяжёлая ситуация", "тяжелая ситуация"],
    {"disabled", "disability", "elderly", "pregnant", "veteran"},
    ["can't afford", "financial hardship", "lost my job", "in the hospital"],
)
_BUILTIN_POSITIVE = (
    {"спасибо", "благодарить", "отлично", "замечательно", "помочь", "решить"},
    ["большое спасибо", "вы помогли", "проблема решена"],
    {"thank", "grateful", "appreciate", "great", "excellent", "resolved"},
    ["thank you so much", "that helped", "problem solved", "issue resolved"],
)

_DEFAULT_QUESTION_PATTERN = (
    "[?？]|уточн|напиш|укаж|подскаж|сообщ|пришл|назов"
    "|provide|specify|send|tell me|what is|could you"
)


def _get_kw(category: str, builtin: tuple) -> tuple[set[str], list[str], set[str], list[str]]:
    """Get keywords from config, falling back to built-in defaults."""
    loaded = get_keywords(category)
    # If all empty — config section missing, use builtin
    if not any(loaded):
        return builtin
    return loaded


def _legal_kw():
    return _get_kw("legal", _BUILTIN_LEGAL)

def _churn_kw():
    return _get_kw("churn", _BUILTIN_CHURN)

def _human_kw():
    return _get_kw("human", _BUILTIN_HUMAN)

def _escalation_kw():
    return _get_kw("escalation", _BUILTIN_ESCALATION)

def _profanity_kw():
    return _get_kw("profanity", _BUILTIN_PROFANITY)

def _profanity_regex() -> tuple[list[str], list[str]]:
    loaded = get_regex_patterns("profanity")
    if not any(loaded):
        return _BUILTIN_PROFANITY_REGEX
    return loaded

def _publicity_kw():
    return _get_kw("publicity", _BUILTIN_PUBLICITY)

def _repeated_contact_kw():
    return _get_kw("repeated_contact", _BUILTIN_REPEATED_CONTACT)

def _vulnerability_kw():
    return _get_kw("vulnerability", _BUILTIN_VULNERABILITY)

def _positive_kw():
    return _get_kw("positive", _BUILTIN_POSITIVE)


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

# Regex to identify question-like bot messages (loaded from config)
_QUESTION_RE = re.compile(
    get_question_pattern(_DEFAULT_QUESTION_PATTERN),
    re.IGNORECASE,
)


def detect_repeated_bot_questions(
    messages: list[DialogueMessage],
    similarity_threshold: float | None = None,
) -> DetectedPattern | None:
    """Detect when the bot asks the same (or very similar) question repeatedly.

    Uses spaCy POS-based content word extraction to compare what the bot
    is asking for (nouns), ignoring how it asks (verbs).
    """
    if similarity_threshold is None:
        similarity_threshold = get_threshold("repeated_question_similarity", 0.45)

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
    window: int | None = None,
) -> DetectedPattern | None:
    """Detect when the dialogue has no new information exchange.

    Looks at the last `window` messages: if bot keeps asking and user
    keeps complaining without providing new data, that's stagnation.
    """
    if window is None:
        window = int(get_threshold("no_progress_window", 6))

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
    user_threshold = get_threshold("no_progress_user_overlap", 0.3)
    bot_threshold = get_threshold("no_progress_bot_overlap", 0.15)
    if avg_overlap > user_threshold and avg_bot_overlap > bot_threshold:
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
        esc_ru, esc_sub_ru, esc_en, esc_sub_en = _escalation_kw()
        found = _find_keywords(
            text,
            esc_ru, esc_en,
            esc_sub_ru, esc_sub_en,
        )
        score += len(found) * 0.5

        return score

    scores = [_intensity_score(m.text) for m in user_msgs]

    mid = len(scores) // 2
    if mid == 0:
        mid = 1
    first_half = sum(scores[:mid]) / mid
    second_half = sum(scores[mid:]) / len(scores[mid:])

    intensity_delta = get_threshold("escalation_intensity_delta", 0.5)
    if second_half > first_half + intensity_delta:
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

    legal_ru, legal_sub_ru, legal_en, legal_sub_en = _legal_kw()
    for m in recent_user:
        found = _find_keywords(
            m.text,
            legal_ru, legal_en,
            legal_sub_ru, legal_sub_en,
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

    churn_ru, churn_sub_ru, churn_en, churn_sub_en = _churn_kw()
    for m in recent_user:
        found = _find_keywords(
            m.text,
            churn_ru, churn_en,
            churn_sub_ru, churn_sub_en,
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

    human_ru, human_sub_ru, human_en, human_sub_en = _human_kw()
    for m in recent_user:
        found = _find_keywords(
            m.text,
            human_ru, human_en,
            human_sub_ru, human_sub_en,
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


def detect_profanity(
    messages: list[DialogueMessage],
) -> DetectedPattern | None:
    """Detect profanity/insults in user messages.

    Uses lemma matching, substring matching, and regex patterns.
    Always critical severity — signals maximum escalation.
    """
    user_msgs = [m for m in messages if m.role == "user"]
    if not user_msgs:
        return None

    recent_user = user_msgs[-3:]
    all_found: list[str] = []

    prof_ru, prof_sub_ru, prof_en, prof_sub_en = _profanity_kw()
    for m in recent_user:
        found = _find_keywords(
            m.text,
            prof_ru, prof_en,
            prof_sub_ru, prof_sub_en,
        )
        all_found.extend(found)

    # Regex matching for censored/wildcard profanity
    regex_ru, regex_en = _profanity_regex()
    for m in recent_user:
        text = m.text
        for pattern in regex_ru + regex_en:
            try:
                if re.search(pattern, text, re.IGNORECASE):
                    all_found.append(f"regex:{pattern[:20]}")
            except re.error:
                continue

    if not all_found:
        return None

    unique_found = list(set(all_found))
    return DetectedPattern(
        pattern_type="profanity",
        severity="critical",
        confidence=min(0.8 + len(unique_found) * 0.05, 1.0),
        evidence=[f"Profanity detected: {', '.join(unique_found[:5])}"],
        details={"keywords_found": unique_found},
    )


def detect_publicity_threat(
    messages: list[DialogueMessage],
) -> DetectedPattern | None:
    """Detect threats to post negative reviews or go public."""
    user_msgs = [m for m in messages if m.role == "user"]
    if not user_msgs:
        return None

    recent_user = user_msgs[-3:]
    all_found: list[str] = []

    pub_ru, pub_sub_ru, pub_en, pub_sub_en = _publicity_kw()
    for m in recent_user:
        found = _find_keywords(
            m.text,
            pub_ru, pub_en,
            pub_sub_ru, pub_sub_en,
        )
        all_found.extend(found)

    if not all_found:
        return None

    unique_found = list(set(all_found))
    return DetectedPattern(
        pattern_type="publicity_threat",
        severity="critical" if len(unique_found) >= 2 else "warning",
        confidence=min(0.6 + len(unique_found) * 0.1, 1.0),
        evidence=[f"Publicity threat signals: {', '.join(unique_found)}"],
        details={"keywords_found": unique_found},
    )


def detect_vulnerability(
    messages: list[DialogueMessage],
) -> DetectedPattern | None:
    """Detect vulnerability signals — customer in a sensitive situation.

    Scans ALL user messages (vulnerability may be mentioned once, early on).
    Returns info-level severity — a flag for priority routing, not a problem.
    """
    user_msgs = [m for m in messages if m.role == "user"]
    if not user_msgs:
        return None

    all_found: list[str] = []

    vuln_ru, vuln_sub_ru, vuln_en, vuln_sub_en = _vulnerability_kw()
    for m in user_msgs:
        found = _find_keywords(
            m.text,
            vuln_ru, vuln_en,
            vuln_sub_ru, vuln_sub_en,
        )
        all_found.extend(found)

    if not all_found:
        return None

    unique_found = list(set(all_found))
    return DetectedPattern(
        pattern_type="vulnerability",
        severity="info",
        confidence=min(0.6 + len(unique_found) * 0.15, 1.0),
        evidence=[f"Vulnerability signals: {', '.join(unique_found)}"],
        details={"keywords_found": unique_found},
    )


def detect_positive_signal(
    messages: list[DialogueMessage],
) -> DetectedPattern | None:
    """Detect positive/de-escalation signals in recent user messages.

    Only looks at last 2 messages — recent sentiment matters.
    """
    user_msgs = [m for m in messages if m.role == "user"]
    if not user_msgs:
        return None

    recent_user = user_msgs[-2:]
    all_found: list[str] = []

    pos_ru, pos_sub_ru, pos_en, pos_sub_en = _positive_kw()
    for m in recent_user:
        found = _find_keywords(
            m.text,
            pos_ru, pos_en,
            pos_sub_ru, pos_sub_en,
        )
        all_found.extend(found)

    if not all_found:
        return None

    unique_found = list(set(all_found))
    return DetectedPattern(
        pattern_type="positive_signal",
        severity="info",
        confidence=min(0.5 + len(unique_found) * 0.15, 1.0),
        evidence=[f"Positive signals: {', '.join(unique_found)}"],
        details={"keywords_found": unique_found},
    )


def detect_repeated_contact(
    messages: list[DialogueMessage],
    contacts_today: int = 1,
) -> DetectedPattern | None:
    """Detect when user has contacted support multiple times.

    Uses contacts_today metadata if available (high confidence).
    Falls back to keyword detection from message text (lower confidence).
    """
    if contacts_today >= 2:
        return DetectedPattern(
            pattern_type="repeated_contact",
            severity="critical" if contacts_today >= 3 else "warning",
            confidence=1.0,
            evidence=[f"User contacted support {contacts_today} times today"],
            details={"contacts_today": contacts_today},
        )

    # Keyword-based fallback when metadata is not available
    user_msgs = [m for m in messages if m.role == "user"]
    if not user_msgs:
        return None

    all_found: list[str] = []
    rc_ru, rc_sub_ru, rc_en, rc_sub_en = _repeated_contact_kw()
    for m in user_msgs:
        found = _find_keywords(
            m.text,
            rc_ru, rc_en,
            rc_sub_ru, rc_sub_en,
        )
        all_found.extend(found)

    if not all_found:
        return None

    unique_found = list(set(all_found))
    return DetectedPattern(
        pattern_type="repeated_contact",
        severity="warning",
        confidence=min(0.5 + len(unique_found) * 0.1, 0.85),
        evidence=[f"Repeated contact keywords: {', '.join(unique_found)}"],
        details={"keywords_found": unique_found},
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
        lambda: detect_profanity(messages),
        lambda: detect_publicity_threat(messages),
        lambda: detect_vulnerability(messages),
        lambda: detect_positive_signal(messages),
    ]

    patterns: list[DetectedPattern] = []
    for detector in detectors:
        result = detector()
        if result is not None:
            patterns.append(result)

    severity_order = {"critical": 0, "warning": 1, "info": 2}
    patterns.sort(key=lambda p: severity_order.get(p.severity, 99))

    return patterns
