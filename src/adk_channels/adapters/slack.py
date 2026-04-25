"""Slack adapter for adk-channels using Bolt + Socket Mode."""

from __future__ import annotations

import logging
import re
from typing import Any

from adk_channels.adapters.base import BaseChannelAdapter
from adk_channels.config import AdapterConfig
from adk_channels.types import AdapterDirection, ChannelMessage, IncomingMessage, OnIncomingMessage

logger = logging.getLogger("adk_channels.adapters.slack")

MAX_LENGTH = 3000  # Slack block text limit; API limit is 4000 but leave margin


async def create_slack_adapter(config: AdapterConfig) -> BaseChannelAdapter:
    """Factory for creating a Slack adapter."""
    return SlackAdapter(config)


class SlackAdapter(BaseChannelAdapter):
    """Bidirectional Slack adapter using Bolt Socket Mode."""

    direction = AdapterDirection.BIDIRECTIONAL

    def __init__(self, config: AdapterConfig) -> None:
        super().__init__()
        self._config = config
        self._bot_token = str(config.model_extra.get("bot_token", "")) if config.model_extra else ""
        self._app_token = str(config.model_extra.get("app_token", "")) if config.model_extra else ""
        self._allowed_channel_ids: list[str] = (
            list(config.model_extra.get("allowed_channel_ids", [])) if config.model_extra else []
        )
        self._respond_to_mentions_only = bool(
            config.model_extra.get("respond_to_mentions_only", False) if config.model_extra else False
        )
        self._slash_command = str(config.model_extra.get("slash_command", "/adk") if config.model_extra else "/adk")

        if not self._bot_token:
            raise ValueError("Slack adapter requires bot_token (xoxb-...)")
        if not self._app_token:
            raise ValueError("Slack adapter requires app_token (xapp-...)")

        self._socket_client: Any | None = None
        self._web_client: Any | None = None
        self._bot_user_id: str | None = None
        self._on_message: OnIncomingMessage | None = None

    def _is_allowed(self, channel_id: str) -> bool:
        if not self._allowed_channel_ids:
            return True
        return channel_id in self._allowed_channel_ids

    def _strip_bot_mention(self, text: str) -> str:
        if not self._bot_user_id:
            return text
        return re.sub(rf"<@{self._bot_user_id}>\s*", "", text).strip()

    def _build_metadata(self, event: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
        meta = {
            "channel_id": event.get("channel"),
            "user_id": event.get("user"),
            "timestamp": event.get("ts"),
            "thread_ts": event.get("thread_ts"),
            "channel_type": event.get("channel_type"),
        }
        if extra:
            meta.update(extra)
        return meta

    async def send(self, message: ChannelMessage) -> None:
        if not message.text:
            raise ValueError("Slack adapter requires text")

        from slack_sdk.web.async_client import AsyncWebClient

        web = AsyncWebClient(token=self._bot_token)
        prefix = f"*[{(message.source or 'adk')}] *\n" if message.source else ""
        full = prefix + message.text

        # Append thoughts as a Slack blockquote
        thoughts: list[str] = message.metadata.get("thoughts") if message.metadata else []
        if thoughts:
            thought_lines = "\n".join("> " + line for t in thoughts for line in t.split("\n"))
            full += f"\n\n> 💭 *Thinking process*\n{thought_lines}"

        thread_ts = message.metadata.get("thread_ts") if message.metadata else None
        channel = message.recipient

        if len(full) <= MAX_LENGTH:
            await web.chat_postMessage(
                channel=channel,
                text=full,
                thread_ts=thread_ts,
                unfurl_links=False,
                unfurl_media=False,
            )
            return

        # Split long messages at newlines
        remaining = full
        while remaining:
            if len(remaining) <= MAX_LENGTH:
                await web.chat_postMessage(
                    channel=channel,
                    text=remaining,
                    thread_ts=thread_ts,
                    unfurl_links=False,
                    unfurl_media=False,
                )
                break
            split_at = remaining.rfind("\n", 0, MAX_LENGTH)
            if split_at < MAX_LENGTH // 2:
                split_at = MAX_LENGTH
            chunk = remaining[:split_at]
            await web.chat_postMessage(
                channel=channel,
                text=chunk,
                thread_ts=thread_ts,
                unfurl_links=False,
                unfurl_media=False,
            )
            remaining = remaining[split_at:].lstrip("\n")

    async def send_typing(self, recipient: str) -> None:
        # Slack bots don't have typing indicators; no-op
        pass

    async def start(self, on_message: OnIncomingMessage) -> None:
        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        from slack_bolt.async_app import AsyncApp
        from slack_sdk.web.async_client import AsyncWebClient

        self._on_message = on_message
        self._web_client = AsyncWebClient(token=self._bot_token)

        # Resolve bot user ID
        try:
            auth_result = await self._web_client.auth_test()
            self._bot_user_id = auth_result.get("user_id")
        except Exception:
            logger.warning("Could not resolve Slack bot user ID")

        app = AsyncApp(token=self._bot_token)

        @app.event("message")
        async def handle_message(event: dict[str, Any], say: Any, ack: Any) -> None:
            await ack()

            # Ignore bot messages
            if event.get("bot_id") or event.get("subtype") == "bot_message":
                return
            # Ignore edits, deletes, etc.
            if event.get("subtype"):
                return
            if not event.get("text"):
                return

            channel = str(event.get("channel", ""))
            if not channel or not self._is_allowed(channel):
                return

            channel_type = event.get("channel_type")
            text = event.get("text", "")

            # Skip messages that @mention the bot in channels/groups (handled by app_mention)
            if self._bot_user_id and channel_type in ("channel", "group") and f"<@{self._bot_user_id}>" in text:
                return

            # In channels/groups, optionally only respond to @mentions
            if self._respond_to_mentions_only and channel_type in ("channel", "group"):
                return

            thread_ts = event.get("thread_ts")
            sender: str = f"{channel}:{thread_ts}" if thread_ts else channel

            if self._on_message:
                result = self._on_message(
                    IncomingMessage(
                        adapter="slack",
                        sender=sender,
                        text=self._strip_bot_mention(text),
                        metadata=self._build_metadata(event, {"event_type": "message"}),
                    )
                )
                if result is not None:
                    await result

        @app.event("app_mention")
        async def handle_mention(event: dict[str, Any], say: Any, ack: Any) -> None:
            await ack()

            channel = str(event.get("channel", ""))
            if not channel or not self._is_allowed(channel):
                return

            thread_ts = event.get("thread_ts")
            sender: str = f"{channel}:{thread_ts}" if thread_ts else channel

            if self._on_message:
                result = self._on_message(
                    IncomingMessage(
                        adapter="slack",
                        sender=sender,
                        text=self._strip_bot_mention(event.get("text", "")),
                        metadata=self._build_metadata(event, {"event_type": "app_mention"}),
                    )
                )
                if result is not None:
                    await result

        @app.command(self._slash_command)
        async def handle_command(ack: Any, command: dict[str, Any], respond: Any) -> None:
            text = command.get("text", "").strip()
            if not text:
                await ack(text=f"Usage: {self._slash_command} [your message]")
                return

            channel_id = str(command.get("channel_id", ""))
            if not channel_id or not self._is_allowed(channel_id):
                await ack(text="This command is not available in this channel.")
                return

            # Acknowledge immediately (Slack requires <3s response)
            await ack(text="Thinking...")

            if self._on_message:
                result = self._on_message(
                    IncomingMessage(
                        adapter="slack",
                        sender=channel_id,
                        text=text,
                        metadata={
                            "channel_id": channel_id,
                            "channel_name": command.get("channel_name"),
                            "user_id": command.get("user_id"),
                            "user_name": command.get("user_name"),
                            "event_type": "slash_command",
                            "command": command.get("command"),
                        },
                    )
                )
                if result is not None:
                    await result

        handler = AsyncSocketModeHandler(app, self._app_token)
        await handler.connect_async()  # type: ignore[no-untyped-call]
        self._socket_client = handler

    async def stop(self) -> None:
        if self._socket_client:
            await self._socket_client.close_async()
            self._socket_client = None
