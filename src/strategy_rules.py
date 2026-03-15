"""
Strategy rule engine for support dialogues.

Maps detected patterns to concrete strategy recommendations.
Purely deterministic — no LLM calls.
"""

from __future__ import annotations

from .models import (
    ActionStep,
    DetectedPattern,
    DialogueMessage,
    EscalationThreshold,
    StrategyResult,
    UserMetadata,
)


# ─── Phrase Tracking ─────────────────────────────────────────────────────────

def _extract_bot_phrases(messages: list[DialogueMessage]) -> dict[str, int]:
    """Count how many times the bot used common empathy/apology phrases."""
    phrases_ru = [
        "понимаю ваше",
        "понимаю ваш",
        "приносим извинения",
        "извините",
        "извиняемся",
        "сожалеем",
        "нам очень жаль",
        "понимаю раздражение",
        "понимаю ваше беспокойство",
        "понимаю ваше неудобство",
    ]
    phrases_en = [
        "i understand your",
        "we apologize",
        "sorry for",
        "i'm sorry",
        "we're sorry",
        "we regret",
        "i apologize",
    ]

    counts: dict[str, int] = {}
    for m in messages:
        if m.role != "bot":
            continue
        text_lower = m.text.lower()
        for phrase in phrases_ru + phrases_en:
            if phrase in text_lower:
                counts[phrase] = counts.get(phrase, 0) + 1

    return counts


def _extract_bot_questions(messages: list[DialogueMessage]) -> list[str]:
    """Extract questions the bot has asked (for anti-patterns)."""
    import re
    questions: list[str] = []
    question_pattern = re.compile(
        r"(уточните|напишите|укажите|подскажите|сообщите|назовите|пришлите|"
        r"provide|specify|send|tell me|could you|what is)[^.!?]*[?？]?",
        re.IGNORECASE,
    )
    for m in messages:
        if m.role != "bot":
            continue
        for match in question_pattern.finditer(m.text):
            questions.append(match.group().strip()[:100])
    return questions


# ─── Strategy Builders ───────────────────────────────────────────────────────

def _build_repeated_question_strategy(
    pattern: DetectedPattern,
    available_actions: list[str],
    language: str,
) -> StrategyResult:
    """Strategy when bot keeps asking the same question."""
    count = pattern.details.get("count", 2)

    actions: list[ActionStep] = [
        ActionStep(
            action="acknowledge_repetition",
            priority="required",
            note="Признать, что бот уже спрашивал это. Одно предложение."
            if language == "ru" else
            "Acknowledge that the bot already asked this. One sentence.",
        ),
    ]

    # Suggest alternative identification methods
    alt_actions = [a for a in available_actions if a not in ("request_order_number",)]
    lookup_actions = [a for a in alt_actions if "lookup" in a or "search" in a or "check" in a]

    if lookup_actions:
        actions.append(ActionStep(
            action=lookup_actions[0],
            priority="primary",
            note="Предложить альтернативный способ поиска."
            if language == "ru" else
            "Offer an alternative lookup method.",
        ))

    if "escalate_to_human" in available_actions:
        actions.append(ActionStep(
            action="escalate_to_human",
            priority="fallback",
            note="Если альтернатива не сработает — передать человеку."
            if language == "ru" else
            "If the alternative doesn't work — transfer to a human.",
        ))

    return StrategyResult(
        recommended_strategy="alternative_identification",
        reasoning=(
            f"Бот {count} раз задал один и тот же вопрос без результата. "
            "Пользователь либо не знает ответ, либо отказывается отвечать. "
            "Необходимо сменить подход."
            if language == "ru" else
            f"Bot asked the same question {count} times without result. "
            "User either doesn't know the answer or refuses to respond. "
            "Need to change approach."
        ),
        action_sequence=actions,
        anti_patterns=[],  # filled by caller
        escalation=EscalationThreshold(
            should_escalate_now=count >= 4,
            escalate_after_n_more_turns=1 if count < 4 else 0,
            reason=(
                "Если альтернативная идентификация не даст результата — эскалация обязательна"
                if language == "ru" else
                "If alternative identification fails — escalation is mandatory"
            ),
        ),
        detected_patterns=[pattern],
    )


def _build_legal_threat_strategy(
    pattern: DetectedPattern,
    available_actions: list[str],
    language: str,
) -> StrategyResult:
    """Strategy when user mentions legal action."""
    actions: list[ActionStep] = [
        ActionStep(
            action="acknowledge_right",
            priority="required",
            note="Подтвердить право клиента на обращение. Формальный тон. Без спора."
            if language == "ru" else
            "Acknowledge the customer's right to complain. Formal tone. No argument.",
        ),
    ]

    if "escalate_to_human" in available_actions or "escalate_to_supervisor" in available_actions:
        esc_action = "escalate_to_supervisor" if "escalate_to_supervisor" in available_actions else "escalate_to_human"
        actions.append(ActionStep(
            action=esc_action,
            priority="required",
            note="Немедленная передача руководству. Не продолжать диалог ботом."
            if language == "ru" else
            "Immediate transfer to supervisor. Do not continue with bot.",
        ))

    return StrategyResult(
        recommended_strategy="immediate_supervisor_escalation",
        reasoning=(
            "Пользователь упомянул юридические действия. Бот не должен продолжать — "
            "необходима немедленная передача компетентному специалисту."
            if language == "ru" else
            "User mentioned legal action. Bot must not continue — "
            "immediate transfer to a competent specialist is required."
        ),
        action_sequence=actions,
        anti_patterns=[],
        escalation=EscalationThreshold(
            should_escalate_now=True,
            reason=(
                "Юридическая угроза — эскалация немедленно"
                if language == "ru" else
                "Legal threat — escalate immediately"
            ),
        ),
        detected_patterns=[pattern],
    )


def _build_human_request_strategy(
    pattern: DetectedPattern,
    available_actions: list[str],
    language: str,
) -> StrategyResult:
    """Strategy when user explicitly asks for a human agent."""
    actions: list[ActionStep] = []

    if "escalate_to_human" in available_actions or "escalate_to_supervisor" in available_actions:
        esc_action = "escalate_to_supervisor" if "escalate_to_supervisor" in available_actions else "escalate_to_human"
        actions.append(ActionStep(
            action=esc_action,
            priority="required",
            note="Пользователь явно попросил оператора — выполнить без промедления."
            if language == "ru" else
            "User explicitly requested a human — comply without delay.",
        ))
    if "offer_callback" in available_actions:
        actions.append(ActionStep(
            action="offer_callback",
            priority="fallback",
            note="Если передача невозможна прямо сейчас — предложить обратный звонок."
            if language == "ru" else
            "If transfer is not possible right now — offer a callback.",
        ))

    return StrategyResult(
        recommended_strategy="comply_with_human_request",
        reasoning=(
            "Пользователь явно запросил живого оператора. "
            "Попытки продолжить диалог ботом ухудшат ситуацию."
            if language == "ru" else
            "User explicitly requested a live agent. "
            "Continuing with a bot will worsen the situation."
        ),
        action_sequence=actions,
        anti_patterns=[],
        escalation=EscalationThreshold(
            should_escalate_now=True,
            reason=(
                "Явный запрос оператора"
                if language == "ru" else
                "Explicit request for human agent"
            ),
        ),
        detected_patterns=[pattern],
    )


def _build_churn_strategy(
    pattern: DetectedPattern,
    available_actions: list[str],
    language: str,
) -> StrategyResult:
    """Strategy when user signals they're about to leave."""
    actions: list[ActionStep] = [
        ActionStep(
            action="acknowledge_frustration",
            priority="required",
            note="Одно предложение. Показать, что ситуация воспринимается серьёзно."
            if language == "ru" else
            "One sentence. Show the situation is taken seriously.",
        ),
    ]

    if "provide_compensation" in available_actions:
        actions.append(ActionStep(
            action="provide_compensation",
            priority="primary",
            note="Предложить конкретную компенсацию (скидка, бонус)."
            if language == "ru" else
            "Offer specific compensation (discount, bonus).",
        ))

    if "escalate_to_human" in available_actions:
        actions.append(ActionStep(
            action="escalate_to_human",
            priority="primary",
            note="Предложить связь с менеджером для решения вопроса."
            if language == "ru" else
            "Offer connection to a manager to resolve the issue.",
        ))

    return StrategyResult(
        recommended_strategy="retention",
        reasoning=(
            "Пользователь сигнализирует об уходе. Необходимо перейти от стандартного "
            "скрипта к удержанию: конкретные действия, компенсация, эскалация."
            if language == "ru" else
            "User signals they're leaving. Need to switch from standard script "
            "to retention: concrete actions, compensation, escalation."
        ),
        action_sequence=actions,
        anti_patterns=[],
        escalation=EscalationThreshold(
            should_escalate_now=False,
            escalate_after_n_more_turns=1,
            reason=(
                "Если удержание не сработает за 1 ход — эскалация"
                if language == "ru" else
                "If retention doesn't work in 1 turn — escalate"
            ),
        ),
        detected_patterns=[pattern],
    )


def _build_escalation_strategy(
    pattern: DetectedPattern,
    available_actions: list[str],
    language: str,
) -> StrategyResult:
    """Strategy when user emotion is escalating."""
    actions: list[ActionStep] = [
        ActionStep(
            action="slow_down",
            priority="required",
            note="Снизить темп. Короткие предложения. Не зеркалить агрессию."
            if language == "ru" else
            "Slow down. Short sentences. Don't mirror aggression.",
        ),
        ActionStep(
            action="validate_emotion",
            priority="required",
            note="Признать чувства без оценки. Не говорить 'успокойтесь'."
            if language == "ru" else
            "Validate feelings without judgment. Don't say 'calm down'.",
        ),
    ]

    if "escalate_to_human" in available_actions:
        actions.append(ActionStep(
            action="escalate_to_human",
            priority="fallback",
            note="Предложить оператора, если ситуация не улучшится."
            if language == "ru" else
            "Offer a human agent if the situation doesn't improve.",
        ))

    return StrategyResult(
        recommended_strategy="de_escalation",
        reasoning=(
            "Эмоциональный тон пользователя нарастает. Бот должен замедлиться, "
            "признать эмоции и предложить конкретное действие вместо шаблонных ответов."
            if language == "ru" else
            "User's emotional tone is escalating. Bot should slow down, "
            "validate emotions and offer a concrete action instead of template responses."
        ),
        action_sequence=actions,
        anti_patterns=[],
        escalation=EscalationThreshold(
            should_escalate_now=False,
            escalate_after_n_more_turns=2,
            reason=(
                "Если эскалация продолжится ещё 2 хода — передать человеку"
                if language == "ru" else
                "If escalation continues for 2 more turns — transfer to human"
            ),
        ),
        detected_patterns=[pattern],
    )


def _build_no_progress_strategy(
    pattern: DetectedPattern,
    available_actions: list[str],
    language: str,
) -> StrategyResult:
    """Strategy when dialogue is stuck without progress."""
    turns = pattern.details.get("turns_without_progress", 3)

    actions: list[ActionStep] = [
        ActionStep(
            action="summarize_situation",
            priority="required",
            note="Кратко изложить что известно и что не удалось выяснить."
            if language == "ru" else
            "Briefly summarize what's known and what couldn't be determined.",
        ),
    ]

    if "escalate_to_human" in available_actions:
        actions.append(ActionStep(
            action="escalate_to_human",
            priority="primary" if turns >= 3 else "fallback",
            note="Передать оператору с полным контекстом."
            if language == "ru" else
            "Transfer to agent with full context.",
        ))

    return StrategyResult(
        recommended_strategy="break_deadlock",
        reasoning=(
            f"Диалог стоит на месте уже {turns} ходов. "
            "Обе стороны повторяют одно и то же. Нужно либо сменить подход, "
            "либо передать человеку."
            if language == "ru" else
            f"Dialogue has been stuck for {turns} turns. "
            "Both sides are repeating themselves. Need to either change approach "
            "or transfer to a human."
        ),
        action_sequence=actions,
        anti_patterns=[],
        escalation=EscalationThreshold(
            should_escalate_now=turns >= 4,
            escalate_after_n_more_turns=1 if turns < 4 else 0,
            reason=(
                "Диалог зашёл в тупик"
                if language == "ru" else
                "Dialogue is deadlocked"
            ),
        ),
        detected_patterns=[pattern],
    )


def _build_repeated_contact_strategy(
    pattern: DetectedPattern,
    available_actions: list[str],
    language: str,
) -> StrategyResult:
    """Strategy when user has contacted support multiple times today."""
    contacts = pattern.details.get("contacts_today", 2)

    actions: list[ActionStep] = [
        ActionStep(
            action="acknowledge_repeated_contact",
            priority="required",
            note="Показать, что мы видим повторное обращение. Не игнорировать."
            if language == "ru" else
            "Show that we see the repeated contact. Don't ignore it.",
        ),
    ]

    if "escalate_to_human" in available_actions:
        actions.append(ActionStep(
            action="escalate_to_human",
            priority="required" if contacts >= 3 else "primary",
            note="Повторное обращение = приоритетная обработка."
            if language == "ru" else
            "Repeated contact = priority handling.",
        ))

    return StrategyResult(
        recommended_strategy="priority_handling",
        reasoning=(
            f"Пользователь обращается {contacts}-й раз за день. "
            "Стандартный скрипт явно не работает — нужна приоритетная обработка."
            if language == "ru" else
            f"User is contacting for the {contacts}th time today. "
            "Standard script clearly doesn't work — priority handling needed."
        ),
        action_sequence=actions,
        anti_patterns=[],
        escalation=EscalationThreshold(
            should_escalate_now=contacts >= 3,
            escalate_after_n_more_turns=1 if contacts < 3 else 0,
            reason=(
                f"{contacts}-е обращение за день"
                if language == "ru" else
                f"{contacts}th contact today"
            ),
        ),
        detected_patterns=[pattern],
    )


# ─── Strategy Selection ──────────────────────────────────────────────────────

# Priority of patterns — which one drives the primary strategy
_PATTERN_PRIORITY: dict[str, int] = {
    "legal_threat": 0,
    "human_request": 1,
    "repeated_contact": 2,
    "repeated_question": 3,
    "no_progress": 4,
    "churn_signal": 5,
    "emotion_escalation": 6,
}

_STRATEGY_BUILDERS: dict[str, callable] = {
    "legal_threat": _build_legal_threat_strategy,
    "human_request": _build_human_request_strategy,
    "repeated_contact": _build_repeated_contact_strategy,
    "repeated_question": _build_repeated_question_strategy,
    "no_progress": _build_no_progress_strategy,
    "churn_signal": _build_churn_strategy,
    "emotion_escalation": _build_escalation_strategy,
}


def _build_default_strategy(
    messages: list[DialogueMessage],
    available_actions: list[str],
    language: str,
) -> StrategyResult:
    """Default strategy when no critical patterns are detected."""
    return StrategyResult(
        recommended_strategy="continue_normally",
        reasoning=(
            "Критических паттернов не обнаружено. Диалог идёт штатно."
            if language == "ru" else
            "No critical patterns detected. Dialogue proceeding normally."
        ),
        action_sequence=[],
        anti_patterns=[],
        escalation=EscalationThreshold(),
        detected_patterns=[],
    )


def _build_anti_patterns(
    messages: list[DialogueMessage],
    patterns: list[DetectedPattern],
    language: str,
) -> list[str]:
    """Build anti-pattern list based on detected patterns and phrase history."""
    anti: list[str] = []

    # Don't repeat overused phrases
    phrase_counts = _extract_bot_phrases(messages)
    for phrase, count in phrase_counts.items():
        if count >= 2:
            if language == "ru":
                anti.append(f"НЕ использовать '{phrase}' — уже было {count} раз")
            else:
                anti.append(f"Do NOT use '{phrase}' — already used {count} times")

    # Don't re-ask questions from repeated_question patterns
    for p in patterns:
        if p.pattern_type == "repeated_question":
            bot_questions = _extract_bot_questions(messages)
            for q in bot_questions[-2:]:
                if language == "ru":
                    anti.append(f"НЕ спрашивать снова: '{q}'")
                else:
                    anti.append(f"Do NOT ask again: '{q}'")

    # General anti-patterns for specific scenarios
    for p in patterns:
        if p.pattern_type == "emotion_escalation":
            if language == "ru":
                anti.append("НЕ говорить 'успокойтесь' или 'не нервничайте'")
                anti.append("НЕ зеркалить агрессию")
            else:
                anti.append("Do NOT say 'calm down' or 'don't worry'")
                anti.append("Do NOT mirror aggression")

        if p.pattern_type == "legal_threat":
            if language == "ru":
                anti.append("НЕ спорить с клиентом о его правах")
                anti.append("НЕ давать юридических советов")
            else:
                anti.append("Do NOT argue with the customer about their rights")
                anti.append("Do NOT give legal advice")

    return anti


def suggest_strategy(
    messages: list[DialogueMessage],
    patterns: list[DetectedPattern],
    available_actions: list[str] | None = None,
    user_metadata: UserMetadata | None = None,
    language: str = "ru",
) -> StrategyResult:
    """Select and build the best strategy based on detected patterns.

    Uses the highest-priority pattern to drive the primary strategy,
    then enriches it with anti-patterns from all detected patterns.
    """
    if available_actions is None:
        available_actions = []

    if not patterns:
        result = _build_default_strategy(messages, available_actions, language)
        result.anti_patterns = _build_anti_patterns(messages, patterns, language)
        return result

    # Sort by pattern priority
    sorted_patterns = sorted(
        patterns,
        key=lambda p: _PATTERN_PRIORITY.get(p.pattern_type, 99),
    )

    # Build strategy from highest-priority pattern
    primary = sorted_patterns[0]
    builder = _STRATEGY_BUILDERS.get(primary.pattern_type)

    if builder:
        result = builder(primary, available_actions, language)
    else:
        result = _build_default_strategy(messages, available_actions, language)

    # Include all detected patterns
    result.detected_patterns = patterns

    # Build anti-patterns from all patterns + phrase history
    result.anti_patterns = _build_anti_patterns(messages, patterns, language)

    # Merge escalation: if any pattern triggers immediate escalation, do it
    for p in patterns:
        if p.pattern_type in ("legal_threat", "human_request") and p.severity == "critical":
            result.escalation.should_escalate_now = True

    return result
