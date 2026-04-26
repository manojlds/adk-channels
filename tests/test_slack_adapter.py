"""Slack adapter unit tests that don't require Slack SDK network calls."""

from __future__ import annotations

import pytest

from adk_channels.adapters.slack import SlackAdapter, _coerce_bool
from adk_channels.config import AdapterConfig
from adk_channels.types import ChannelMessage


class _FakeSlackResponse(dict):
    def __init__(self, headers: dict[str, str], **values: str) -> None:
        super().__init__(values)
        self.headers = headers


class _FakeSlackWebClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.reactions: list[dict[str, str]] = []

    async def chat_postMessage(self, **kwargs: object) -> None:  # noqa: N802
        self.messages.append(kwargs)

    async def reactions_add(self, channel: str, timestamp: str, name: str) -> None:
        self.reactions.append({"channel": channel, "timestamp": timestamp, "name": name})


def _make_adapter() -> SlackAdapter:
    return SlackAdapter(
        AdapterConfig(
            type="slack",
            bot_token="xoxb-test",
            app_token="xapp-test",
        )
    )


def _make_allowlisted_adapter(channel_ids: list[str]) -> SlackAdapter:
    return SlackAdapter(
        AdapterConfig(
            type="slack",
            bot_token="xoxb-test",
            app_token="xapp-test",
            allowed_channel_ids=channel_ids,
        )
    )


def test_resolve_destination_uses_recipient_thread_suffix() -> None:
    adapter = _make_adapter()
    channel, thread_ts = adapter._resolve_destination(
        ChannelMessage(adapter="slack", recipient="C123:1746044940.000100", text="hello")
    )
    assert channel == "C123"
    assert thread_ts == "1746044940.000100"


def test_resolve_destination_prefers_metadata_thread() -> None:
    adapter = _make_adapter()
    channel, thread_ts = adapter._resolve_destination(
        ChannelMessage(
            adapter="slack",
            recipient="C123:ignored",
            text="hello",
            metadata={"thread_ts": "1746044940.999999"},
        )
    )
    assert channel == "C123"
    assert thread_ts == "1746044940.999999"


def test_translate_channel_app_mention_defaults_to_thread_session() -> None:
    adapter = _make_adapter()
    adapter._bot_user_id = "B123"

    incoming = adapter._translate_event(
        {
            "channel": "C123",
            "channel_type": "channel",
            "user": "U123",
            "ts": "1746044940.123400",
            "text": "<@B123> deploy this",
        },
        "app_mention",
    )

    assert incoming is not None
    assert incoming.sender == "C123:1746044940.123400"
    assert incoming.text == "deploy this"
    assert incoming.metadata["thread_ts"] == "1746044940.123400"
    assert incoming.metadata["event_type"] == "app_mention"


def test_translate_channel_app_mention_keeps_existing_thread() -> None:
    adapter = _make_adapter()

    incoming = adapter._translate_event(
        {
            "channel": "C123",
            "channel_type": "channel",
            "user": "U123",
            "ts": "1746044941.000001",
            "thread_ts": "1746044940.123400",
            "text": "follow up",
        },
        "app_mention",
    )

    assert incoming is not None
    assert incoming.sender == "C123:1746044940.123400"
    assert incoming.metadata["thread_ts"] == "1746044940.123400"


def test_translate_channel_message_bot_mention_defaults_to_thread_session() -> None:
    adapter = _make_adapter()
    adapter._bot_user_id = "B123"

    incoming = adapter._translate_event(
        {
            "channel": "C123",
            "channel_type": "channel",
            "user": "U123",
            "ts": "1746044940.123400",
            "text": "<@B123> deploy this",
        },
        "message",
    )

    assert incoming is not None
    assert incoming.sender == "C123:1746044940.123400"
    assert incoming.text == "deploy this"
    assert incoming.metadata["event_type"] == "app_mention"
    assert incoming.metadata["thread_ts"] == "1746044940.123400"


def test_mentions_only_marks_thread_continuation_as_existing_session_required() -> None:
    adapter = SlackAdapter(
        AdapterConfig(
            type="slack",
            bot_token="xoxb-test",
            app_token="xapp-test",
            respond_to_mentions_only=True,
        )
    )
    adapter._bot_user_id = "B123"

    event = {
        "channel": "C123",
        "channel_type": "channel",
        "user": "U456",
        "ts": "1746044941.000001",
        "thread_ts": "1746044940.123400",
        "text": "continue without mentioning the bot",
    }

    assert adapter._should_handle_message_event(event) is True
    follow_up = adapter._translate_event(event, "message")
    assert follow_up is not None
    assert follow_up.sender == "C123:1746044940.123400"
    assert follow_up.metadata["event_type"] == "message"
    assert follow_up.metadata["requires_existing_session"] is True


def test_mentions_only_ignores_top_level_channel_message_without_mention() -> None:
    adapter = SlackAdapter(
        AdapterConfig(
            type="slack",
            bot_token="xoxb-test",
            app_token="xapp-test",
            respond_to_mentions_only=True,
        )
    )

    assert (
        adapter._should_handle_message_event(
            {
                "channel": "C123",
                "channel_type": "channel",
                "user": "U123",
                "ts": "1746044940.123400",
                "text": "not for the bot",
            }
        )
        is False
    )


def test_deduplicates_message_and_app_mention_events_for_same_slack_message() -> None:
    adapter = _make_adapter()
    event = {
        "channel": "C123",
        "channel_type": "channel",
        "user": "U123",
        "ts": "1746044940.123400",
        "text": "<@B123> deploy this",
    }

    assert adapter._claim_event(event) is True
    assert adapter._claim_event(event) is False


def test_translate_dm_app_mention_does_not_create_thread() -> None:
    adapter = _make_adapter()

    incoming = adapter._translate_event(
        {
            "channel": "D123",
            "channel_type": "im",
            "user": "U123",
            "ts": "1746044940.123400",
            "text": "hello",
        },
        "app_mention",
    )

    assert incoming is not None
    assert incoming.sender == "D123"
    assert incoming.metadata["thread_ts"] is None


def test_translate_threaded_dm_message_uses_thread_session() -> None:
    adapter = _make_adapter()

    incoming = adapter._translate_event(
        {
            "channel": "D123",
            "channel_type": "im",
            "user": "U123",
            "ts": "1746044941.000001",
            "thread_ts": "1746044940.123400",
            "text": "thread follow up",
        },
        "message",
    )

    assert incoming is not None
    assert incoming.sender == "D123:1746044940.123400"
    assert incoming.metadata["thread_ts"] == "1746044940.123400"


def test_translate_channel_app_mention_can_disable_default_threading() -> None:
    adapter = SlackAdapter(
        AdapterConfig(
            type="slack",
            bot_token="xoxb-test",
            app_token="xapp-test",
            reply_in_thread_by_default="false",
        )
    )

    incoming = adapter._translate_event(
        {
            "channel": "C123",
            "channel_type": "channel",
            "user": "U123",
            "ts": "1746044940.123400",
            "text": "hello",
        },
        "app_mention",
    )

    assert incoming is not None
    assert incoming.sender == "C123"
    assert incoming.metadata["thread_ts"] is None


def test_slack_boolean_config_coerces_env_style_strings() -> None:
    adapter = SlackAdapter(
        AdapterConfig(
            type="slack",
            bot_token="xoxb-test",
            app_token="xapp-test",
            respond_to_mentions_only="true",
            reply_in_thread_by_default="false",
            continue_threads_without_mention="false",
        )
    )

    assert adapter._respond_to_mentions_only is True
    assert adapter._reply_in_thread_by_default is False
    assert adapter._continue_threads_without_mention is False


def test_slack_boolean_config_uses_default_for_unknown_values() -> None:
    assert _coerce_bool("maybe", False) is False
    assert _coerce_bool("maybe", True) is True
    assert _coerce_bool(1, False) is False


def test_extracts_slack_scopes_from_auth_response_header() -> None:
    response = _FakeSlackResponse(
        {"x-oauth-scopes": "chat:write, app_mentions:read, reactions:write"},
        user_id="U123",
        team_id="T123",
    )

    assert SlackAdapter._extract_granted_scopes(response) == {
        "app_mentions:read",
        "chat:write",
        "reactions:write",
    }


def test_startup_scope_check_fails_when_required_scope_missing() -> None:
    adapter = _make_adapter()

    with pytest.raises(RuntimeError, match="chat:write"):
        adapter._validate_scope_check({"app_mentions:read"})


def test_startup_capabilities_are_derived_from_scopes() -> None:
    capabilities = SlackAdapter._build_capabilities(
        {"app_mentions:read", "chat:write", "im:history", "reactions:write"}
    )

    assert capabilities["send_messages"] is True
    assert capabilities["app_mentions"] is True
    assert capabilities["direct_messages"] is True
    assert capabilities["reactions"] is True
    assert capabilities["file_downloads"] is False


@pytest.mark.asyncio
async def test_send_adds_completed_reaction_when_supported() -> None:
    adapter = SlackAdapter(
        AdapterConfig(
            type="slack",
            bot_token="xoxb-test",
            app_token="xapp-test",
            completed_reaction="white_check_mark",
        )
    )
    fake_web = _FakeSlackWebClient()
    adapter._web_client = fake_web
    adapter._capabilities["reactions"] = True

    await adapter.send(
        ChannelMessage(
            adapter="slack",
            recipient="C123:1746044940.123400",
            text="done",
            metadata={"channel_id": "C123", "timestamp": "1746044941.000001"},
        )
    )

    assert fake_web.messages[0]["channel"] == "C123"
    assert fake_web.reactions == [{"channel": "C123", "timestamp": "1746044941.000001", "name": "white_check_mark"}]


@pytest.mark.asyncio
async def test_send_skips_completed_reaction_without_reactions_scope() -> None:
    adapter = SlackAdapter(
        AdapterConfig(
            type="slack",
            bot_token="xoxb-test",
            app_token="xapp-test",
            completed_reaction="white_check_mark",
        )
    )
    fake_web = _FakeSlackWebClient()
    adapter._web_client = fake_web

    await adapter.send(
        ChannelMessage(
            adapter="slack",
            recipient="C123:1746044940.123400",
            text="done",
            metadata={"channel_id": "C123", "timestamp": "1746044941.000001"},
        )
    )

    assert fake_web.messages
    assert fake_web.reactions == []


@pytest.mark.asyncio
async def test_add_processing_reaction_when_supported() -> None:
    adapter = SlackAdapter(
        AdapterConfig(
            type="slack",
            bot_token="xoxb-test",
            app_token="xapp-test",
            processing_reaction="eyes",
        )
    )
    fake_web = _FakeSlackWebClient()
    adapter._web_client = fake_web
    adapter._capabilities["reactions"] = True

    await adapter._add_processing_reaction({"channel": "C123", "ts": "1746044941.000001"})

    assert fake_web.reactions == [{"channel": "C123", "timestamp": "1746044941.000001", "name": "eyes"}]


@pytest.mark.asyncio
async def test_add_processing_reaction_skips_without_reactions_scope() -> None:
    adapter = SlackAdapter(
        AdapterConfig(
            type="slack",
            bot_token="xoxb-test",
            app_token="xapp-test",
            processing_reaction="eyes",
        )
    )
    fake_web = _FakeSlackWebClient()
    adapter._web_client = fake_web

    await adapter._add_processing_reaction({"channel": "C123", "ts": "1746044941.000001"})

    assert fake_web.reactions == []


def test_build_tool_blocks_formats_interactions() -> None:
    adapter = _make_adapter()
    blocks = adapter._build_tool_blocks(
        [
            {"type": "tool_call", "name": "search_docs", "payload": '{"query":"sessions"}'},
            {"type": "tool_result", "name": "search_docs", "payload": "Found 3 documents"},
        ]
    )
    assert len(blocks) == 2
    assert blocks[0]["type"] == "section"
    assert "Tool call" in blocks[0]["text"]["text"]
    assert "Tool result" in blocks[1]["text"]["text"]


def test_translate_block_action_maps_to_incoming_message() -> None:
    adapter = _make_adapter()
    incoming = adapter._translate_block_action(
        {
            "type": "block_actions",
            "channel": {"id": "C123", "name": "ops"},
            "user": {"id": "U123", "username": "alice"},
            "message": {"ts": "1746044940.123400", "text": "Run tool?"},
            "actions": [
                {
                    "action_id": "tool.run",
                    "type": "button",
                    "block_id": "tool_actions",
                    "value": "run_now",
                    "action_ts": "1746044941.000001",
                }
            ],
            "response_url": "https://hooks.slack.com/actions/123",
            "trigger_id": "1337.7331",
        }
    )

    assert incoming is not None
    assert incoming.adapter == "slack"
    assert incoming.sender == "C123:1746044940.123400"
    assert incoming.text == "action:tool.run value:run_now"
    assert incoming.metadata["event_type"] == "block_action"
    assert incoming.metadata["thread_ts"] == "1746044940.123400"


def test_translate_block_action_uses_select_values() -> None:
    adapter = _make_adapter()
    incoming = adapter._translate_block_action(
        {
            "type": "block_actions",
            "channel": {"id": "C123"},
            "user": {"id": "U123"},
            "message": {"ts": "1746044940.123400", "thread_ts": "1746044900.000001"},
            "actions": [
                {
                    "action_id": "tool.pick",
                    "type": "static_select",
                    "selected_option": {
                        "value": "lookup_logs",
                        "text": {"type": "plain_text", "text": "Lookup logs"},
                    },
                }
            ],
        }
    )

    assert incoming is not None
    assert incoming.sender == "C123:1746044900.000001"
    assert incoming.text == "action:tool.pick value:lookup_logs"


def test_translate_block_action_respects_channel_allowlist() -> None:
    adapter = _make_allowlisted_adapter(["C999"])
    incoming = adapter._translate_block_action(
        {
            "type": "block_actions",
            "channel": {"id": "C123"},
            "user": {"id": "U123"},
            "message": {"ts": "1746044940.123400"},
            "actions": [{"action_id": "tool.run", "type": "button", "value": "run_now"}],
        }
    )

    assert incoming is None


def test_translate_block_action_parses_tool_action_convention() -> None:
    adapter = _make_adapter()
    incoming = adapter._translate_block_action(
        {
            "type": "block_actions",
            "channel": {"id": "C123"},
            "user": {"id": "U123"},
            "message": {"ts": "1746044940.123400"},
            "actions": [
                {
                    "action_id": "adk.tool.logs.open",
                    "type": "button",
                    "value": "latest",
                }
            ],
        }
    )

    assert incoming is not None
    assert incoming.metadata["tool_name"] == "logs"
    assert incoming.metadata["tool_action"] == "open"


def test_build_outbound_blocks_merges_custom_actions_and_tool_blocks() -> None:
    adapter = _make_adapter()
    blocks = adapter._build_outbound_blocks(
        {
            "slack_blocks": [{"type": "divider"}],
            "slack_actions_text": "Choose next action",
            "slack_actions": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Run"},
                    "action_id": "adk.tool.ci.run",
                    "value": "quick",
                }
            ],
        },
        [{"type": "tool_call", "name": "ci", "payload": '{"suite":"quick"}'}],
    )

    assert len(blocks) == 4
    assert blocks[0]["type"] == "divider"
    assert blocks[1]["type"] == "section"
    assert blocks[1]["text"]["text"] == "Choose next action"
    assert blocks[2]["type"] == "actions"
    assert blocks[3]["type"] == "section"


def test_build_outbound_blocks_uses_structured_tool_payload() -> None:
    adapter = _make_adapter()
    blocks = adapter._build_outbound_blocks(
        {},
        [
            {
                "type": "tool_result",
                "name": "request_delete_file",
                "payload": '{"status":"pending_approval"}',
                "raw_payload": {
                    "message": "Approval requested.",
                    "slack_actions_text": "Approve delete?",
                    "slack_actions": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Approve"},
                            "action_id": "adk.tool.approval.approve",
                            "value": '{"request_id":"req-1"}',
                        }
                    ],
                },
            }
        ],
    )

    assert len(blocks) == 2
    assert blocks[0]["type"] == "section"
    assert blocks[0]["text"]["text"] == "Approve delete?"
    assert blocks[1]["type"] == "actions"


def test_build_outbound_blocks_supports_nested_slack_payload() -> None:
    adapter = _make_adapter()
    blocks = adapter._build_outbound_blocks(
        {},
        [
            {
                "type": "tool_result",
                "name": "request_file_options",
                "raw_payload": {
                    "message": "Waiting for selection",
                    "slack": {
                        "actions_text": "Choose files",
                        "actions": [
                            {
                                "type": "multi_static_select",
                                "action_id": "adk.tool.options.choose",
                                "placeholder": {"type": "plain_text", "text": "Select files"},
                                "options": [
                                    {
                                        "text": {"type": "plain_text", "text": "a.txt"},
                                        "value": "a.txt",
                                    }
                                ],
                            }
                        ],
                    },
                },
            }
        ],
    )

    assert len(blocks) == 2
    assert blocks[0]["type"] == "section"
    assert blocks[0]["text"]["text"] == "Choose files"
    assert blocks[1]["type"] == "actions"
