"""Helpers to build structured tool UI payloads for channel rendering."""

from __future__ import annotations

from typing import Any

from adk_channels.slack_interactions import build_tool_action_id, build_tool_button


def _normalize_select_option(option: str | tuple[str, str] | dict[str, Any]) -> dict[str, Any]:
    if isinstance(option, str):
        label = option
        value = option
        return {
            "text": {"type": "plain_text", "text": label[:75]},
            "value": value[:75],
        }

    if isinstance(option, tuple):
        label, value = option
        return {
            "text": {"type": "plain_text", "text": str(label)[:75]},
            "value": str(value)[:75],
        }

    if isinstance(option, dict):
        if "text" in option and "value" in option:
            text_value = option["text"]
            normalized_text = (
                text_value if isinstance(text_value, dict) else {"type": "plain_text", "text": str(text_value)}
            )
            return {
                "text": {
                    "type": normalized_text.get("type", "plain_text"),
                    "text": str(normalized_text.get("text", ""))[:75],
                },
                "value": str(option.get("value", ""))[:75],
            }

        label = str(option.get("label") or option.get("name") or option.get("value") or "option")
        value = str(option.get("value") or label)
        normalized: dict[str, Any] = {
            "text": {"type": "plain_text", "text": label[:75]},
            "value": value[:75],
        }
        description = option.get("description")
        if description:
            normalized["description"] = {
                "type": "plain_text",
                "text": str(description)[:75],
            }
        return normalized

    return {
        "text": {"type": "plain_text", "text": "option"},
        "value": "option",
    }


def tool_info(message: str, *, status: str = "ok", **extra: Any) -> dict[str, Any]:
    """Build a plain informational tool response payload."""
    payload: dict[str, Any] = {
        "status": status,
        "message": message,
    }
    payload.update(extra)
    return payload


def tool_approval(
    *,
    message: str,
    tool_name: str,
    value: str | dict[str, Any] | None = None,
    approve_action: str = "approve",
    reject_action: str = "reject",
    approve_label: str = "Approve",
    reject_label: str = "Reject",
    actions_text: str | None = None,
    block_id: str = "adk_tool_approval",
    status: str = "pending_approval",
) -> dict[str, Any]:
    """Build a tool payload that renders as an approval prompt in Slack."""
    return {
        "status": status,
        "message": message,
        "slack": {
            "actions_text": actions_text or message,
            "actions_block_id": block_id,
            "actions": [
                build_tool_button(
                    label=approve_label,
                    tool_name=tool_name,
                    action=approve_action,
                    value=value,
                    style="primary",
                ),
                build_tool_button(
                    label=reject_label,
                    tool_name=tool_name,
                    action=reject_action,
                    value=value,
                    style="danger",
                ),
            ],
        },
    }


def tool_single_select(
    *,
    message: str,
    tool_name: str,
    action: str,
    options: list[str | tuple[str, str] | dict[str, Any]],
    placeholder: str = "Select an option",
    actions_text: str | None = None,
    block_id: str = "adk_tool_single_select",
    status: str = "pending_selection",
) -> dict[str, Any]:
    """Build a tool payload that renders a Slack single-select."""
    normalized_options = [_normalize_select_option(option) for option in options][:100]

    select_element = {
        "type": "static_select",
        "placeholder": {"type": "plain_text", "text": placeholder[:150]},
        "action_id": build_tool_action_id(tool_name, action),
        "options": normalized_options,
    }

    return {
        "status": status,
        "message": message,
        "slack": {
            "actions_text": actions_text or message,
            "actions_block_id": block_id,
            "actions": [select_element],
        },
    }


def tool_multi_select(
    *,
    message: str,
    tool_name: str,
    action: str,
    options: list[str | tuple[str, str] | dict[str, Any]],
    placeholder: str = "Select one or more options",
    max_selected_items: int = 5,
    actions_text: str | None = None,
    block_id: str = "adk_tool_multi_select",
    status: str = "pending_selection",
) -> dict[str, Any]:
    """Build a tool payload that renders a Slack multi-select."""
    normalized_options = [_normalize_select_option(option) for option in options][:100]

    select_element = {
        "type": "multi_static_select",
        "placeholder": {"type": "plain_text", "text": placeholder[:150]},
        "action_id": build_tool_action_id(tool_name, action),
        "options": normalized_options,
        "max_selected_items": max(1, min(max_selected_items, 100)),
    }

    return {
        "status": status,
        "message": message,
        "slack": {
            "actions_text": actions_text or message,
            "actions_block_id": block_id,
            "actions": [select_element],
        },
    }
