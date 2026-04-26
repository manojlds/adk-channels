"""Tests for ADK event translation helpers."""

from __future__ import annotations

from adk_channels.adk_events import fallback_response_from_tool_interactions


def test_fallback_response_prefers_message_field() -> None:
    response = fallback_response_from_tool_interactions(
        [
            {
                "type": "tool_result",
                "name": "request_delete_file",
                "raw_payload": {
                    "message": "Approval requested for config.yaml.",
                },
            }
        ]
    )
    assert response == "Approval requested for config.yaml."


def test_fallback_response_uses_nested_slack_message() -> None:
    response = fallback_response_from_tool_interactions(
        [
            {
                "type": "tool_result",
                "name": "request_options",
                "raw_payload": {
                    "status": "pending_selection",
                    "slack": {
                        "message": "Choose one or more files.",
                    },
                },
            }
        ]
    )
    assert response == "Choose one or more files."
