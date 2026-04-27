"""Slack adapter for adk-channels using Bolt + Socket Mode."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from adk_channels.adapters.base import BaseChannelAdapter
from adk_channels.config import AdapterConfig
from adk_channels.slack_interactions import parse_tool_action_id
from adk_channels.types import AdapterDirection, ChannelMessage, IncomingMessage, OnIncomingMessage

logger = logging.getLogger("adk_channels.adapters.slack")

MAX_LENGTH = 3000  # Slack block text limit; API limit is 4000 but leave margin
EVENT_DEDUPE_TTL_SECONDS = 300
EVENT_DEDUPE_PRUNE_INTERVAL_SECONDS = 30
EVENT_DEDUPE_MAX_KEYS = 10_000
DEFAULT_PROCESSING_REACTION = "eyes"
DEFAULT_COMPLETED_REACTION = "white_check_mark"
REQUIRED_BOT_SCOPES = frozenset({"app_mentions:read", "chat:write"})
CAPABILITY_SCOPES = {
    "send_messages": frozenset({"chat:write"}),
    "app_mentions": frozenset({"app_mentions:read"}),
    "direct_messages": frozenset({"im:history"}),
    "public_channel_messages": frozenset({"channels:history"}),
    "private_channel_messages": frozenset({"groups:history"}),
    "multi_person_direct_messages": frozenset({"mpim:history"}),
    "slash_commands": frozenset({"commands"}),
    "reactions": frozenset({"reactions:write"}),
    "file_downloads": frozenset({"files:read"}),
    "file_uploads": frozenset({"files:write"}),
    "user_lookup": frozenset({"users:read"}),
}


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"", "0", "false", "no", "off"}:
            return False
        return default
    return default


def _coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def create_slack_adapter(config: AdapterConfig) -> BaseChannelAdapter:
    """Factory for creating a Slack adapter."""
    return SlackAdapter(config)


class SlackAdapter(BaseChannelAdapter):
    """Bidirectional Slack adapter using Bolt Socket Mode."""

    direction = AdapterDirection.BIDIRECTIONAL

    def __init__(self, config: AdapterConfig) -> None:
        super().__init__()
        model_extra = config.model_extra or {}
        self._config = config
        self._bot_token = str(model_extra.get("bot_token", ""))
        self._app_token = str(model_extra.get("app_token", ""))
        self._allowed_channel_ids: list[str] = list(model_extra.get("allowed_channel_ids", [])) if model_extra else []
        self._respond_to_mentions_only = _coerce_bool(model_extra.get("respond_to_mentions_only"), False)
        self._reply_in_thread_by_default = _coerce_bool(model_extra.get("reply_in_thread_by_default"), True)
        self._continue_threads_without_mention = _coerce_bool(model_extra.get("continue_threads_without_mention"), True)
        self._slash_command = str(model_extra.get("slash_command", "/adk"))
        self._processing_reaction_explicit = "processing_reaction" in model_extra
        self._completed_reaction_explicit = "completed_reaction" in model_extra
        self._processing_reaction = _coerce_optional_str(
            model_extra.get("processing_reaction", DEFAULT_PROCESSING_REACTION)
        )
        self._completed_reaction = _coerce_optional_str(
            model_extra.get("completed_reaction", DEFAULT_COMPLETED_REACTION)
        )

        if not self._bot_token:
            raise ValueError("Slack adapter requires bot_token (xoxb-...)")
        if not self._app_token:
            raise ValueError("Slack adapter requires app_token (xapp-...)")

        self._socket_client: Any | None = None
        self._web_client: Any | None = None
        self._bot_user_id: str | None = None
        self._team_id: str | None = None
        self._on_message: OnIncomingMessage | None = None
        self._processed_event_keys: dict[str, float] = {}
        self._last_event_prune_at = 0.0
        self._granted_scopes: set[str] = set()
        self._capabilities: dict[str, bool] = dict.fromkeys(CAPABILITY_SCOPES, False)

    def get_status(self) -> dict[str, Any]:
        """Return Slack adapter startup-discovered status.

        Keys:
        - ``team_id``: Slack workspace/team ID, if authenticated.
        - ``bot_user_id``: Slack bot user ID, if authenticated.
        - ``granted_scopes``: sorted bot token OAuth scopes detected from Slack.
        - ``capabilities``: capability name to availability, derived from granted scopes.
        """
        return {
            "team_id": self._team_id,
            "bot_user_id": self._bot_user_id,
            "granted_scopes": sorted(self._granted_scopes),
            "capabilities": dict(self._capabilities),
        }

    def _is_allowed(self, channel_id: str) -> bool:
        if not self._allowed_channel_ids:
            return True
        return channel_id in self._allowed_channel_ids

    @staticmethod
    def _get_header(response: Any, name: str) -> str | None:
        headers = getattr(response, "headers", None)
        if headers is None:
            return None

        target = name.lower()

        for key in (name, name.lower(), name.upper()):
            value = headers.get(key) if hasattr(headers, "get") else None
            if value is None:
                continue
            if isinstance(value, list | tuple):
                value = ",".join(str(item) for item in value)
            return str(value)

        if hasattr(headers, "items"):
            for key, value in headers.items():
                if str(key).lower() != target:
                    continue
                if isinstance(value, list | tuple):
                    value = ",".join(str(item) for item in value)
                return str(value)
        return None

    @classmethod
    def _extract_granted_scopes(cls, response: Any) -> set[str]:
        scopes_header = cls._get_header(response, "x-oauth-scopes")
        if not scopes_header:
            return set()
        return {scope.strip() for scope in scopes_header.split(",") if scope.strip()}

    @staticmethod
    def _build_capabilities(scopes: set[str]) -> dict[str, bool]:
        return {name: required.issubset(scopes) for name, required in CAPABILITY_SCOPES.items()}

    @staticmethod
    def _missing_required_scopes(scopes: set[str]) -> list[str]:
        return sorted(REQUIRED_BOT_SCOPES - scopes)

    def _configured_reactions(self) -> list[str]:
        return [name for name in (self._processing_reaction, self._completed_reaction) if name]

    def _explicitly_configured_reactions(self) -> list[str]:
        reactions: list[str] = []
        if self._processing_reaction_explicit and self._processing_reaction:
            reactions.append(self._processing_reaction)
        if self._completed_reaction_explicit and self._completed_reaction:
            reactions.append(self._completed_reaction)
        return reactions

    def _validate_scope_check(self, scopes: set[str]) -> None:
        if not scopes:
            raise RuntimeError(
                "Slack startup check failed: could not inspect bot token scopes from the x-oauth-scopes response header"
            )

        missing_required = self._missing_required_scopes(scopes)
        if missing_required:
            required = ", ".join(sorted(REQUIRED_BOT_SCOPES))
            missing = ", ".join(missing_required)
            raise RuntimeError(
                "Slack startup check failed: bot token is missing required scopes "
                f"({missing}). Minimum required bot scopes: {required}"
            )

    def _log_scope_capabilities(self) -> None:
        enabled = sorted(name for name, available in self._capabilities.items() if available)
        disabled = sorted(name for name, available in self._capabilities.items() if not available)
        logger.info(
            "Slack startup check passed team=%s bot_user_id=%s capabilities=%s unavailable=%s",
            self._team_id or "unknown",
            self._bot_user_id or "unknown",
            enabled,
            disabled,
        )

        if self._configured_reactions() and not self._capabilities.get("reactions", False):
            explicit_reactions = self._explicitly_configured_reactions()
            if explicit_reactions:
                logger.warning(
                    "Slack reactions configured (%s) but bot token lacks reactions:write; reactions are disabled",
                    ", ".join(explicit_reactions),
                )
            else:
                logger.info("Slack reactions are unavailable without reactions:write; default reactions are disabled")

        if self._slash_command and not self._capabilities.get("slash_commands", False):
            logger.warning(
                "Slack slash command %s is configured but bot token lacks commands; slash commands are disabled",
                self._slash_command,
            )

        if not self._capabilities.get("direct_messages", False):
            logger.info("Slack direct messages are unavailable without im:history")

        if self._continue_threads_without_mention and not (
            self._capabilities.get("public_channel_messages", False)
            or self._capabilities.get("private_channel_messages", False)
        ):
            logger.warning(
                "Slack thread continuation without @mention needs channels:history and/or groups:history message events"
            )

    async def _run_startup_checks(self, web: Any) -> None:
        try:
            auth_result = await web.auth_test()
        except Exception as exc:
            raise RuntimeError("Slack startup check failed: bot_token could not be authenticated") from exc

        self._bot_user_id = _coerce_optional_str(auth_result.get("user_id"))
        self._team_id = _coerce_optional_str(auth_result.get("team_id"))
        self._granted_scopes = self._extract_granted_scopes(auth_result)
        self._validate_scope_check(self._granted_scopes)
        self._capabilities = self._build_capabilities(self._granted_scopes)
        self._log_scope_capabilities()

    def _strip_bot_mention(self, text: str) -> str:
        if not self._bot_user_id:
            return text
        return re.sub(rf"<@{self._bot_user_id}>\s*", "", text).strip()

    def _is_bot_mention(self, event: dict[str, Any]) -> bool:
        if not self._bot_user_id:
            return False
        return f"<@{self._bot_user_id}>" in str(event.get("text") or "")

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

    @staticmethod
    def _is_direct_message(event: dict[str, Any]) -> bool:
        channel_type = str(event.get("channel_type") or "")
        if channel_type == "im":
            return True

        channel = str(event.get("channel") or "")
        return not channel_type and channel.startswith("D")

    @staticmethod
    def _event_key(event: dict[str, Any]) -> str | None:
        channel = event.get("channel")
        message_ts = event.get("ts")
        if channel in (None, "") or message_ts in (None, ""):
            return None
        return f"{channel}:{message_ts}"

    def _prune_tracking(self, now: float) -> None:
        self._processed_event_keys = {
            key: seen_at
            for key, seen_at in self._processed_event_keys.items()
            if now - seen_at <= EVENT_DEDUPE_TTL_SECONDS
        }
        self._last_event_prune_at = now

    def _should_prune_tracking(self, now: float) -> bool:
        return (
            now - self._last_event_prune_at >= EVENT_DEDUPE_PRUNE_INTERVAL_SECONDS
            or len(self._processed_event_keys) > EVENT_DEDUPE_MAX_KEYS
        )

    @staticmethod
    def _is_thread_reply(event: dict[str, Any]) -> bool:
        thread_ts = event.get("thread_ts")
        if thread_ts in (None, ""):
            return False

        message_ts = event.get("ts")
        if message_ts in (None, ""):
            return True

        return str(thread_ts) != str(message_ts)

    def _claim_event(self, event: dict[str, Any]) -> bool:
        now = time.time()
        if self._should_prune_tracking(now):
            self._prune_tracking(now)

        event_key = self._event_key(event)
        if event_key is None:
            return True

        seen_at = self._processed_event_keys.get(event_key)
        if seen_at is not None and now - seen_at <= EVENT_DEDUPE_TTL_SECONDS:
            return False

        self._processed_event_keys[event_key] = now
        return True

    def _effective_event_type(self, event: dict[str, Any], event_type: str) -> str:
        if event_type == "message" and not self._is_direct_message(event) and self._is_bot_mention(event):
            return "app_mention"
        return event_type

    def _requires_existing_session(self, event: dict[str, Any], event_type: str) -> bool:
        return (
            event_type == "message"
            and self._respond_to_mentions_only
            and self._continue_threads_without_mention
            and not self._is_direct_message(event)
            and not self._is_bot_mention(event)
            and self._is_thread_reply(event)
        )

    def _resolve_event_thread_ts(self, event: dict[str, Any], event_type: str) -> str | None:
        effective_event_type = self._effective_event_type(event, event_type)
        thread_ts = event.get("thread_ts")
        if thread_ts:
            return str(thread_ts)

        if (
            effective_event_type == "app_mention"
            and self._reply_in_thread_by_default
            and not self._is_direct_message(event)
        ):
            message_ts = event.get("ts")
            if message_ts:
                return str(message_ts)

        return None

    def _should_handle_message_event(self, event: dict[str, Any]) -> bool:
        if self._is_direct_message(event):
            return True

        if self._is_bot_mention(event):
            return True

        if self._continue_threads_without_mention and self._is_thread_reply(event):
            return True

        return not self._respond_to_mentions_only

    def _translate_event(self, event: dict[str, Any], event_type: str) -> IncomingMessage | None:
        channel = str(event.get("channel") or "")
        if not channel or not self._is_allowed(channel):
            return None

        effective_event_type = self._effective_event_type(event, event_type)
        thread_ts = self._resolve_event_thread_ts(event, event_type)
        sender = f"{channel}:{thread_ts}" if thread_ts else channel
        metadata_extra: dict[str, Any] = {"event_type": effective_event_type, "thread_ts": thread_ts}
        if self._requires_existing_session(event, event_type):
            metadata_extra["requires_existing_session"] = True

        return IncomingMessage(
            adapter="slack",
            sender=sender,
            text=self._strip_bot_mention(str(event.get("text") or "")),
            metadata=self._build_metadata(event, metadata_extra),
        )

    def _resolve_destination(self, message: ChannelMessage) -> tuple[str, str | None]:
        channel = message.recipient
        thread_ts = message.metadata.get("thread_ts") if message.metadata else None

        if ":" in channel:
            maybe_channel, maybe_thread = channel.split(":", 1)
            if maybe_channel and maybe_thread:
                channel = maybe_channel
                if thread_ts is None:
                    thread_ts = maybe_thread

        return channel, str(thread_ts) if thread_ts is not None else None

    async def _add_reaction(self, channel: str | None, timestamp: str | None, reaction: str | None) -> None:
        if not channel or not timestamp or not reaction or not self._capabilities.get("reactions", False):
            return

        web = self._web_client
        if web is None:
            return

        try:
            await web.reactions_add(channel=channel, timestamp=timestamp, name=reaction)
        except Exception:
            logger.debug("Failed to add Slack reaction %s", reaction, exc_info=True)

    async def _remove_reaction(self, channel: str | None, timestamp: str | None, reaction: str | None) -> None:
        if not channel or not timestamp or not reaction or not self._capabilities.get("reactions", False):
            return

        web = self._web_client
        if web is None:
            return

        try:
            await web.reactions_remove(channel=channel, timestamp=timestamp, name=reaction)
        except Exception:
            logger.debug("Failed to remove Slack reaction %s", reaction, exc_info=True)

    async def _add_processing_reaction(self, event: dict[str, Any]) -> None:
        await self._add_reaction(
            _coerce_optional_str(event.get("channel")),
            _coerce_optional_str(event.get("ts")),
            self._processing_reaction,
        )

    async def _add_completed_reaction(self, metadata: dict[str, Any]) -> None:
        """Add the configured completion reaction to the originating Slack message."""
        # message_ts comes from block actions; timestamp comes from regular message events.
        channel = _coerce_optional_str(metadata.get("channel_id"))
        timestamp = _coerce_optional_str(metadata.get("message_ts") or metadata.get("timestamp"))
        await self._remove_reaction(channel, timestamp, self._processing_reaction)
        await self._add_reaction(channel, timestamp, self._completed_reaction)

    @staticmethod
    def _format_tool_interaction(interaction: dict[str, Any]) -> str:
        interaction_type = str(interaction.get("type", "tool"))
        name = str(interaction.get("name", "tool"))
        payload = str(interaction.get("payload", "")).strip()
        if len(payload) > 180:
            payload = f"{payload[:177]}..."

        if interaction_type == "tool_call":
            return f":gear: *Tool call* `{name}`\n`{payload or '(no args)'}`"
        if interaction_type in {"tool_result", "code_result"}:
            return f":white_check_mark: *Tool result* `{name}`\n`{payload or '(no output)'}`"
        if interaction_type == "code":
            return f":keyboard: *Code execution* `{name}`\n`{payload or '(no code)'}`"
        return f":information_source: *Tool event* `{name}`\n`{payload or '(empty)'}`"

    def _build_tool_blocks(self, interactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for interaction in interactions[:8]:
            if not isinstance(interaction, dict):
                continue
            if self._extract_slack_payload(interaction.get("raw_payload")) is not None:
                continue
            line = self._format_tool_interaction(interaction)
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": line[:3000]}})
        return blocks

    def _build_actions_block_from_metadata(self, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        actions_raw = metadata.get("slack_actions")
        if not isinstance(actions_raw, list):
            actions_raw = metadata.get("actions")
        if not isinstance(actions_raw, list):
            return []

        action_elements = [element for element in actions_raw if isinstance(element, dict)][:25]
        if not action_elements:
            return []

        blocks: list[dict[str, Any]] = []
        actions_text = metadata.get("slack_actions_text")
        if not isinstance(actions_text, str):
            actions_text = metadata.get("actions_text")
        if isinstance(actions_text, str) and actions_text.strip():
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": actions_text[:3000],
                    },
                }
            )

        block_id = str(metadata.get("slack_actions_block_id") or metadata.get("actions_block_id") or "adk_tool_actions")
        blocks.append(
            {
                "type": "actions",
                "block_id": block_id[:255],
                "elements": action_elements,
            }
        )
        return blocks

    @staticmethod
    def _extract_slack_payload(raw_payload: Any) -> dict[str, Any] | None:
        if not isinstance(raw_payload, dict):
            return None

        slack_candidate = raw_payload.get("slack")
        slack_payload = slack_candidate if isinstance(slack_candidate, dict) else raw_payload

        if any(
            key in slack_payload
            for key in (
                "slack_blocks",
                "slack_actions",
                "slack_actions_text",
                "slack_actions_block_id",
                "blocks",
                "actions",
                "actions_text",
                "actions_block_id",
            )
        ):
            return slack_payload

        return None

    def _build_tool_structured_blocks(self, interactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []

        for interaction in interactions[:8]:
            if not isinstance(interaction, dict):
                continue

            interaction_type = str(interaction.get("type") or "")
            if interaction_type != "tool_result":
                continue

            slack_payload = self._extract_slack_payload(interaction.get("raw_payload"))
            if slack_payload is None:
                continue

            custom_blocks_raw = slack_payload.get("slack_blocks")
            if not isinstance(custom_blocks_raw, list):
                custom_blocks_raw = slack_payload.get("blocks")

            if isinstance(custom_blocks_raw, list):
                blocks.extend(block for block in custom_blocks_raw if isinstance(block, dict))

            blocks.extend(self._build_actions_block_from_metadata(slack_payload))

        return blocks[:50]

    def _build_outbound_blocks(
        self,
        metadata: dict[str, Any],
        tool_interactions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        custom_blocks_raw = metadata.get("slack_blocks")
        custom_blocks = (
            [block for block in custom_blocks_raw if isinstance(block, dict)]
            if isinstance(custom_blocks_raw, list)
            else []
        )

        action_blocks = self._build_actions_block_from_metadata(metadata)
        tool_structured_blocks = self._build_tool_structured_blocks(tool_interactions)
        tool_blocks = self._build_tool_blocks(tool_interactions)

        combined = [*custom_blocks, *action_blocks, *tool_structured_blocks, *tool_blocks]
        return combined[:50]

    @staticmethod
    def _resolve_action_thread_ts(body: dict[str, Any]) -> str | None:
        message_raw = body.get("message")
        message = message_raw if isinstance(message_raw, dict) else {}

        thread_ts = message.get("thread_ts")
        if thread_ts:
            return str(thread_ts)

        message_ts = message.get("ts")
        if message_ts:
            return str(message_ts)

        container_raw = body.get("container")
        container = container_raw if isinstance(container_raw, dict) else {}

        container_thread_ts = container.get("thread_ts")
        if container_thread_ts:
            return str(container_thread_ts)

        container_message_ts = container.get("message_ts")
        if container_message_ts:
            return str(container_message_ts)

        return None

    @staticmethod
    def _extract_option_value(option: dict[str, Any]) -> str | None:
        option_value = option.get("value")
        if option_value not in (None, ""):
            return str(option_value)

        text_raw = option.get("text")
        if isinstance(text_raw, dict):
            text = text_raw.get("text")
            if text not in (None, ""):
                return str(text)

        return None

    def _extract_action_value(self, action: dict[str, Any]) -> str:
        direct_value = action.get("value")
        if direct_value not in (None, ""):
            return str(direct_value)

        selected_option_raw = action.get("selected_option")
        if isinstance(selected_option_raw, dict):
            selected_option_value = self._extract_option_value(selected_option_raw)
            if selected_option_value:
                return selected_option_value

        selected_options_raw = action.get("selected_options")
        if isinstance(selected_options_raw, list):
            selected_values: list[str] = []
            for selected_option in selected_options_raw:
                if isinstance(selected_option, dict):
                    selected_option_value = self._extract_option_value(selected_option)
                    if selected_option_value:
                        selected_values.append(selected_option_value)
            if selected_values:
                return ",".join(selected_values)

        for key in (
            "selected_user",
            "selected_channel",
            "selected_conversation",
            "selected_date",
            "selected_time",
            "selected_datetime",
        ):
            selected_value = action.get(key)
            if selected_value not in (None, ""):
                return str(selected_value)

        return ""

    def _build_action_text(self, action: dict[str, Any]) -> str:
        action_id = str(action.get("action_id") or "interactive_action")
        action_value = self._extract_action_value(action)
        if action_value:
            return f"action:{action_id} value:{action_value}"
        return f"action:{action_id}"

    def _translate_block_action(self, body: dict[str, Any]) -> IncomingMessage | None:
        if str(body.get("type") or "") != "block_actions":
            return None

        actions_raw = body.get("actions")
        if not isinstance(actions_raw, list) or not actions_raw:
            return None

        action_raw = actions_raw[0]
        if not isinstance(action_raw, dict):
            return None

        channel_raw = body.get("channel")
        channel = channel_raw if isinstance(channel_raw, dict) else {}

        container_raw = body.get("container")
        container = container_raw if isinstance(container_raw, dict) else {}

        channel_id_any = channel.get("id") or container.get("channel_id")
        if channel_id_any in (None, ""):
            return None

        channel_id = str(channel_id_any)
        if not self._is_allowed(channel_id):
            return None

        thread_ts = self._resolve_action_thread_ts(body)
        sender = f"{channel_id}:{thread_ts}" if thread_ts else channel_id

        user_raw = body.get("user")
        user = user_raw if isinstance(user_raw, dict) else {}

        message_raw = body.get("message")
        message = message_raw if isinstance(message_raw, dict) else {}

        action_value = self._extract_action_value(action_raw)
        action_id = str(action_raw.get("action_id") or "")
        metadata: dict[str, Any] = {
            "channel_id": channel_id,
            "user_id": user.get("id"),
            "user_name": user.get("username") or user.get("name"),
            "timestamp": action_raw.get("action_ts"),
            "thread_ts": thread_ts,
            "event_type": "block_action",
            "action_id": action_id,
            "action_type": action_raw.get("type"),
            "block_id": action_raw.get("block_id"),
            "action_value": action_value,
            "message_ts": message.get("ts") or container.get("message_ts"),
            "message_text": message.get("text"),
            "response_url": body.get("response_url"),
            "trigger_id": body.get("trigger_id"),
        }

        parsed_tool_action = parse_tool_action_id(action_id)
        if parsed_tool_action is not None:
            tool_name, tool_action = parsed_tool_action
            metadata["tool_name"] = tool_name
            metadata["tool_action"] = tool_action

        return IncomingMessage(
            adapter="slack",
            sender=sender,
            text=self._build_action_text(action_raw),
            metadata=metadata,
        )

    async def send(self, message: ChannelMessage) -> None:
        if not message.text:
            raise ValueError("Slack adapter requires text")

        if self._web_client is None:
            from slack_sdk.web.async_client import AsyncWebClient

            self._web_client = AsyncWebClient(token=self._bot_token)

        web = self._web_client
        if web is None:
            raise RuntimeError("Slack web client is not initialized")

        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        prefix = f"*[{(message.source or 'adk')}] *\n" if message.source else ""
        full = prefix + message.text

        # Prepend thoughts as a Slack blockquote (before the response)
        thoughts_raw = metadata.get("thoughts")
        thoughts: list[str] = thoughts_raw if isinstance(thoughts_raw, list) else []
        if thoughts:
            thought_lines = "\n".join("> " + line for t in thoughts for line in t.split("\n"))
            full = f"> 💭 *Thinking process*\n{thought_lines}\n\n{full}"

        tool_interactions_raw = metadata.get("tool_interactions")
        tool_interactions: list[dict[str, Any]] = (
            tool_interactions_raw if isinstance(tool_interactions_raw, list) else []
        )
        channel, thread_ts = self._resolve_destination(message)

        if len(full) <= MAX_LENGTH:
            blocks = self._build_outbound_blocks(metadata, tool_interactions)
            if blocks:
                await web.chat_postMessage(
                    channel=channel,
                    text=full,
                    thread_ts=thread_ts,
                    unfurl_links=False,
                    unfurl_media=False,
                    blocks=blocks,
                )
            else:
                await web.chat_postMessage(
                    channel=channel,
                    text=full,
                    thread_ts=thread_ts,
                    unfurl_links=False,
                    unfurl_media=False,
                )
            await self._add_completed_reaction(metadata)
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

        await self._add_completed_reaction(metadata)

    async def send_typing(self, recipient: str) -> None:
        # Slack bots don't have typing indicators; no-op
        pass

    async def start(self, on_message: OnIncomingMessage) -> None:
        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        from slack_bolt.async_app import AsyncApp
        from slack_sdk.web.async_client import AsyncWebClient

        self._on_message = on_message
        self._web_client = AsyncWebClient(token=self._bot_token)

        await self._run_startup_checks(self._web_client)

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

            channel = str(event.get("channel") or "")
            if not channel or not self._is_allowed(channel):
                return

            if not self._should_handle_message_event(event):
                return

            if not self._claim_event(event):
                return

            incoming = self._translate_event(event, "message")
            if incoming is None:
                return

            await self._add_processing_reaction(event)

            if self._on_message:
                result = self._on_message(incoming)
                if result is not None:
                    await result

        @app.event("app_mention")
        async def handle_mention(event: dict[str, Any], say: Any, ack: Any) -> None:
            await ack()

            channel = str(event.get("channel") or "")
            if not channel or not self._is_allowed(channel):
                return

            if not self._claim_event(event):
                return

            incoming = self._translate_event(event, "app_mention")
            if incoming is None:
                return

            await self._add_processing_reaction(event)

            if self._on_message:
                result = self._on_message(incoming)
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

        @app.action(re.compile(".*"))
        async def handle_action(ack: Any, body: dict[str, Any]) -> None:
            await ack()

            incoming = self._translate_block_action(body)
            if incoming is None or not self._on_message:
                return

            result = self._on_message(incoming)
            if result is not None:
                await result

        handler = AsyncSocketModeHandler(app, self._app_token)
        await handler.connect_async()  # type: ignore[no-untyped-call]
        self._socket_client = handler

    async def stop(self) -> None:
        if self._socket_client:
            await self._socket_client.close_async()
            self._socket_client = None
        self._web_client = None
