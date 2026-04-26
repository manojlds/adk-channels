"""Tests for Slack interaction helper utilities."""

from __future__ import annotations

from adk_channels.slack_interactions import (
    TOOL_ACTION_PREFIX,
    build_tool_action_id,
    build_tool_actions_blocks,
    build_tool_button,
    parse_tool_action_id,
)


def test_build_tool_action_id_normalizes_segments() -> None:
    action_id = build_tool_action_id("Log Viewer", "Open:Latest")
    assert action_id == f"{TOOL_ACTION_PREFIX}.Log_Viewer.Open_Latest"


def test_parse_tool_action_id_round_trip() -> None:
    action_id = build_tool_action_id("deploy", "run")
    parsed = parse_tool_action_id(action_id)
    assert parsed == ("deploy", "run")


def test_parse_tool_action_id_ignores_unknown_prefix() -> None:
    assert parse_tool_action_id("other.tool.deploy.run") is None


def test_build_tool_button_uses_convention_and_json_payload() -> None:
    button = build_tool_button(
        label="Run Quick Tests",
        tool_name="ci",
        action="run_tests",
        value={"suite": "quick"},
        style="primary",
    )
    assert button["type"] == "button"
    assert button["action_id"] == "adk.tool.ci.run_tests"
    assert button["style"] == "primary"
    assert button["value"] == '{"suite": "quick"}'


def test_build_tool_actions_blocks_creates_section_and_actions() -> None:
    blocks = build_tool_actions_blocks(
        prompt_text="Pick an option",
        buttons=[
            build_tool_button(label="Run", tool_name="ci", action="run"),
            build_tool_button(label="Cancel", tool_name="ci", action="cancel", style="danger"),
        ],
        block_id="ci_actions",
    )
    assert len(blocks) == 2
    assert blocks[0]["type"] == "section"
    assert blocks[0]["text"]["text"] == "Pick an option"
    assert blocks[1]["type"] == "actions"
    assert blocks[1]["block_id"] == "ci_actions"
    assert len(blocks[1]["elements"]) == 2
