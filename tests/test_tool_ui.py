"""Tests for tool UI payload helpers."""

from __future__ import annotations

from adk_channels.tool_ui import tool_approval, tool_info, tool_multi_select, tool_single_select


def test_tool_info_payload() -> None:
    payload = tool_info("Done", files=["a.txt"])
    assert payload["status"] == "ok"
    assert payload["message"] == "Done"
    assert payload["files"] == ["a.txt"]


def test_tool_approval_payload_contains_slack_actions() -> None:
    payload = tool_approval(
        message="Approve delete?",
        tool_name="approval",
        value={"request_id": "req-1"},
    )

    assert payload["status"] == "pending_approval"
    assert payload["message"] == "Approve delete?"
    slack = payload["slack"]
    assert slack["actions_text"] == "Approve delete?"
    assert len(slack["actions"]) == 2
    assert slack["actions"][0]["action_id"] == "adk.tool.approval.approve"
    assert slack["actions"][1]["action_id"] == "adk.tool.approval.reject"


def test_tool_single_select_payload() -> None:
    payload = tool_single_select(
        message="Choose one",
        tool_name="picker",
        action="pick",
        options=["one", ("Two", "2")],
    )

    slack = payload["slack"]
    element = slack["actions"][0]
    assert element["type"] == "static_select"
    assert element["action_id"] == "adk.tool.picker.pick"
    assert len(element["options"]) == 2


def test_tool_multi_select_payload() -> None:
    payload = tool_multi_select(
        message="Choose many",
        tool_name="options",
        action="choose",
        options=["a", "b", "c"],
        max_selected_items=2,
    )

    slack = payload["slack"]
    element = slack["actions"][0]
    assert element["type"] == "multi_static_select"
    assert element["action_id"] == "adk.tool.options.choose"
    assert element["max_selected_items"] == 2
