"""Integration tests: full scenario end-to-end through strategy_suggest tool."""

from __future__ import annotations

import json

import pytest

from src.models import StrategySuggestInput, DialogueMessage, UserMetadata
from src.tools.strategy_suggest import strategy_suggest


def _msg(role: str, text: str) -> DialogueMessage:
    return DialogueMessage(role=role, text=text)


class TestScenarioRepeatedOrderNumber:
    """Scenario 1: Bot keeps asking for order number."""

    @pytest.mark.asyncio
    async def test_full_scenario(self):
        params = StrategySuggestInput(
            dialogue_history=[
                _msg("user", "Где мой заказ?"),
                _msg("bot", "Уточните номер заказа."),
                _msg("user", "Какого хрена, доставьте уже!"),
                _msg("bot", "Понимаю раздражение. Напишите номер заказа."),
                _msg("user", "Сделайте что-нибудь, третий раз пишу!"),
            ],
            available_actions=[
                "request_order_number",
                "lookup_by_phone",
                "lookup_by_email",
                "escalate_to_human",
            ],
            language="ru",
        )
        raw = await strategy_suggest(params)
        result = json.loads(raw)

        assert result["recommended_strategy"] == "alternative_identification"

        actions = [a["action"] for a in result["action_sequence"]]
        assert "acknowledge_repetition" in actions
        assert "request_order_number" not in actions

        assert any("НЕ" in ap for ap in result["anti_patterns"])

        assert len(result["detected_patterns"]) > 0


class TestScenarioLegalThreat:
    """Scenario 2: User threatens legal action."""

    @pytest.mark.asyncio
    async def test_full_scenario(self):
        params = StrategySuggestInput(
            dialogue_history=[
                _msg("user", "Пишу заявление в суд, вы пожалеете!"),
            ],
            available_actions=["escalate_to_human", "escalate_to_supervisor"],
            language="ru",
        )
        raw = await strategy_suggest(params)
        result = json.loads(raw)

        assert result["recommended_strategy"] == "immediate_supervisor_escalation"
        assert result["escalation"]["should_escalate_now"] is True

        actions = [a["action"] for a in result["action_sequence"]]
        assert "escalate_to_supervisor" in actions


class TestScenarioSuccessfulLookup:
    """Scenario 3: Normal dialogue, no issues."""

    @pytest.mark.asyncio
    async def test_full_scenario(self):
        params = StrategySuggestInput(
            dialogue_history=[
                _msg("user", "Где заказ?"),
                _msg("bot", "Напишите номер заказа или телефон."),
                _msg("user", "+79161234567"),
            ],
            available_actions=["lookup_by_phone", "provide_order_status"],
            language="ru",
        )
        raw = await strategy_suggest(params)
        result = json.loads(raw)

        assert result["recommended_strategy"] == "continue_normally"
        assert result["escalation"]["should_escalate_now"] is False


class TestScenarioRepeatedContactAngry:
    """Scenario: Third contact today + emotional escalation."""

    @pytest.mark.asyncio
    async def test_full_scenario(self):
        params = StrategySuggestInput(
            dialogue_history=[
                _msg("user", "Опять я! Третий раз пишу! Где мой заказ?!"),
                _msg("bot", "Здравствуйте! Уточните номер заказа."),
                _msg("user", "ДА СКОЛЬКО МОЖНО!!! НЕМЕДЛЕННО РЕШИТЕ!!!"),
            ],
            user_metadata=UserMetadata(total_contacts_today=3),
            available_actions=["escalate_to_human", "lookup_by_phone"],
            language="ru",
        )
        raw = await strategy_suggest(params)
        result = json.loads(raw)

        # Should detect repeated contact + escalation
        pattern_types = {p["pattern_type"] for p in result["detected_patterns"]}
        assert "repeated_contact" in pattern_types

        # Should prioritize escalation for 3rd contact
        assert result["escalation"]["should_escalate_now"] is True or \
               result["escalation"]["escalate_after_n_more_turns"] is not None


class TestScenarioHumanRequestWithChurn:
    """Scenario: User wants human + threatens to leave."""

    @pytest.mark.asyncio
    async def test_full_scenario(self):
        params = StrategySuggestInput(
            dialogue_history=[
                _msg("user", "Переведите на оператора! Отменяю подписку!"),
            ],
            available_actions=["escalate_to_human", "provide_compensation"],
            language="ru",
        )
        raw = await strategy_suggest(params)
        result = json.loads(raw)

        # Human request should take priority over churn
        assert result["recommended_strategy"] == "comply_with_human_request"
        assert result["escalation"]["should_escalate_now"] is True
