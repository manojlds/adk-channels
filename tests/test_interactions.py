"""Tests for interaction routing helpers."""

from __future__ import annotations

import pytest

from adk_channels.interactions import ActionContext, InteractionOutcome, ToolActionRouter, normalize_interaction_result
from adk_channels.types import ChannelMessage, IncomingMessage


def _action_message() -> IncomingMessage:
    return IncomingMessage(
        adapter="slack",
        sender="C123:1746044900.000001",
        text='action:adk.tool.approval.approve value:{"request_id":"req-1"}',
        metadata={
            "event_type": "block_action",
            "action_id": "adk.tool.approval.approve",
            "action_value": '{"request_id":"req-1"}',
            "tool_name": "approval",
            "tool_action": "approve",
        },
    )


def test_action_context_parses_json_and_values() -> None:
    ctx = ActionContext.from_message(
        IncomingMessage(
            adapter="slack",
            sender="C123",
            text="action",
            metadata={
                "event_type": "block_action",
                "action_id": "adk.tool.options.choose",
                "action_value": "a,b,c",
                "tool_name": "options",
                "tool_action": "choose",
            },
        )
    )
    assert ctx.event_type == "block_action"
    assert ctx.action_values() == ["a", "b", "c"]
    assert ctx.action_value_json() == {}


def test_normalize_interaction_result_accepts_string_and_message() -> None:
    message = _action_message()

    from_string = normalize_interaction_result(message, "Done")
    assert from_string is not None
    assert from_string.handled is True
    assert len(from_string.replies) == 1
    assert from_string.replies[0].text == "Done"

    from_message = normalize_interaction_result(
        message,
        ChannelMessage(adapter="slack", recipient="C123", text="Ack"),
    )
    assert from_message is not None
    assert len(from_message.replies) == 1
    assert from_message.replies[0].text == "Ack"


@pytest.mark.asyncio
async def test_tool_action_router_routes_on_tool_name_and_action() -> None:
    router = ToolActionRouter()

    @router.on_tool("approval", "approve")
    def handle_approval(ctx: ActionContext) -> str:
        payload = ctx.action_value_json()
        return f"approved:{payload.get('request_id', '')}"

    outcome = await router.dispatch(_action_message())

    assert outcome is not None
    assert isinstance(outcome, InteractionOutcome)
    assert outcome.handled is True
    assert len(outcome.replies) == 1
    assert outcome.replies[0].text == "approved:req-1"


@pytest.mark.asyncio
async def test_tool_action_router_unhandled_when_no_route() -> None:
    router = ToolActionRouter()
    outcome = await router.dispatch(_action_message())
    assert outcome is None


@pytest.mark.asyncio
async def test_tool_action_router_can_mark_unhandled() -> None:
    router = ToolActionRouter()

    @router.on_tool("approval", "approve")
    def handle_approval(ctx: ActionContext) -> bool:
        return False

    outcome = await router.dispatch(_action_message())
    assert outcome is not None
    assert outcome.handled is False
