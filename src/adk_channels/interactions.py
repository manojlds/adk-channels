"""Interaction routing utilities for Slack and other interactive channels."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from adk_channels.types import ChannelMessage, IncomingMessage


@dataclass
class ActionContext:
    """Context passed to an interaction handler."""

    message: IncomingMessage
    event_type: str = ""
    action_id: str = ""
    action_value: str = ""
    tool_name: str | None = None
    tool_action: str | None = None

    @classmethod
    def from_message(cls, message: IncomingMessage) -> ActionContext:
        metadata = message.metadata if isinstance(message.metadata, dict) else {}

        event_type_any = metadata.get("event_type")
        action_id_any = metadata.get("action_id")
        action_value_any = metadata.get("action_value")
        tool_name_any = metadata.get("tool_name")
        tool_action_any = metadata.get("tool_action")

        return cls(
            message=message,
            event_type=str(event_type_any) if event_type_any is not None else "",
            action_id=str(action_id_any) if action_id_any is not None else "",
            action_value=str(action_value_any) if action_value_any is not None else "",
            tool_name=str(tool_name_any) if tool_name_any is not None else None,
            tool_action=str(tool_action_any) if tool_action_any is not None else None,
        )

    def action_value_json(self) -> dict[str, Any]:
        """Parse action value as JSON object when possible."""
        if not self.action_value:
            return {}

        try:
            parsed = json.loads(self.action_value)
        except json.JSONDecodeError:
            return {}

        return parsed if isinstance(parsed, dict) else {}

    def action_values(self) -> list[str]:
        """Parse CSV-style action value into a list of non-empty values."""
        if not self.action_value:
            return []
        return [value.strip() for value in self.action_value.split(",") if value.strip()]

    def reply(
        self,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
        adapter: str | None = None,
        recipient: str | None = None,
    ) -> ChannelMessage:
        """Build a reply message addressed to the same channel/thread by default."""
        return ChannelMessage(
            adapter=adapter or self.message.adapter,
            recipient=recipient or self.message.sender,
            text=text,
            metadata=metadata or {},
        )


@dataclass
class InteractionOutcome:
    """Outcome from handling an interaction event."""

    handled: bool = True
    replies: list[ChannelMessage] = field(default_factory=list)

    @classmethod
    def unhandled(cls) -> InteractionOutcome:
        return cls(handled=False)


InteractionResult = InteractionOutcome | ChannelMessage | list[ChannelMessage] | str | bool | None
InteractionHandler = Callable[[IncomingMessage], InteractionResult | Awaitable[InteractionResult]]
ToolActionHandler = Callable[[ActionContext], InteractionResult | Awaitable[InteractionResult]]


def normalize_interaction_result(message: IncomingMessage, result: InteractionResult) -> InteractionOutcome | None:
    """Normalize flexible interaction return values into an InteractionOutcome."""
    if result is None:
        return None

    if isinstance(result, InteractionOutcome):
        return result

    if isinstance(result, bool):
        return InteractionOutcome(handled=result)

    if isinstance(result, str):
        return InteractionOutcome(
            handled=True,
            replies=[
                ChannelMessage(
                    adapter=message.adapter,
                    recipient=message.sender,
                    text=result,
                )
            ],
        )

    if isinstance(result, ChannelMessage):
        return InteractionOutcome(handled=True, replies=[result])

    if isinstance(result, list):
        if not all(isinstance(item, ChannelMessage) for item in result):
            raise TypeError("Interaction handler returned a list with non-ChannelMessage items")
        return InteractionOutcome(handled=True, replies=list(result))

    raise TypeError(f"Unsupported interaction handler result type: {type(result).__name__}")


class ToolActionRouter:
    """Router for Slack-style interactive actions.

    Use this as a bridge-level interaction handler:

        router = ToolActionRouter()
        @router.on_tool("approval", "approve")
        def handle_approve(ctx):
            return "Approved"

        bridge = ChatBridge(..., interaction_handler=router)
    """

    def __init__(self, *, event_types: set[str] | None = None) -> None:
        self._event_types = event_types or {"block_action"}
        self._action_handlers: dict[str, ToolActionHandler] = {}
        self._tool_handlers: dict[tuple[str, str], ToolActionHandler] = {}
        self._fallback_handler: ToolActionHandler | None = None

    def register_action(self, action_id: str, handler: ToolActionHandler) -> None:
        self._action_handlers[action_id] = handler

    def register_tool_action(self, tool_name: str, action: str, handler: ToolActionHandler) -> None:
        self._tool_handlers[(tool_name, action)] = handler

    def set_fallback(self, handler: ToolActionHandler) -> None:
        self._fallback_handler = handler

    def on_action(self, action_id: str) -> Callable[[ToolActionHandler], ToolActionHandler]:
        """Decorator to register a handler for an exact action ID."""

        def decorator(handler: ToolActionHandler) -> ToolActionHandler:
            self.register_action(action_id, handler)
            return handler

        return decorator

    def on_tool(self, tool_name: str, action: str) -> Callable[[ToolActionHandler], ToolActionHandler]:
        """Decorator to register a handler for a parsed tool/action pair."""

        def decorator(handler: ToolActionHandler) -> ToolActionHandler:
            self.register_tool_action(tool_name, action, handler)
            return handler

        return decorator

    async def __call__(self, message: IncomingMessage) -> InteractionOutcome | None:
        return await self.dispatch(message)

    async def dispatch(self, message: IncomingMessage) -> InteractionOutcome | None:
        """Dispatch an incoming interaction message to the matching handler."""
        ctx = ActionContext.from_message(message)
        if self._event_types and ctx.event_type not in self._event_types:
            return None

        handler = self._resolve_handler(ctx)
        if handler is None:
            return None

        raw_result = handler(ctx)
        if asyncio.iscoroutine(raw_result):
            result = await raw_result
        else:
            result = raw_result

        normalized = normalize_interaction_result(message, result)
        if normalized is None:
            return InteractionOutcome(handled=True)
        return normalized

    def _resolve_handler(self, ctx: ActionContext) -> ToolActionHandler | None:
        if ctx.action_id and ctx.action_id in self._action_handlers:
            return self._action_handlers[ctx.action_id]

        if ctx.tool_name and ctx.tool_action:
            handler = self._tool_handlers.get((ctx.tool_name, ctx.tool_action))
            if handler is not None:
                return handler

        return self._fallback_handler
