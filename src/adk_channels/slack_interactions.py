"""Helpers for Slack interactive blocks and action ID conventions."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

TOOL_ACTION_PREFIX = "adk.tool"
_INVALID_ACTION_ID_CHARS = re.compile(r"[^a-zA-Z0-9_.-]+")


def _normalize_action_segment(value: str) -> str:
    segment = _INVALID_ACTION_ID_CHARS.sub("_", value.strip())
    segment = segment.strip("._-")
    return segment or "unknown"


def build_tool_action_id(tool_name: str, action: str) -> str:
    """Build a standardized Slack action_id for tool interactions."""
    normalized_tool = _normalize_action_segment(tool_name)
    normalized_action = _normalize_action_segment(action)
    return f"{TOOL_ACTION_PREFIX}.{normalized_tool}.{normalized_action}"


def parse_tool_action_id(action_id: str) -> tuple[str, str] | None:
    """Parse a standardized tool action ID back to (tool_name, action)."""
    prefix = f"{TOOL_ACTION_PREFIX}."
    if not action_id.startswith(prefix):
        return None

    payload = action_id[len(prefix) :]
    if "." not in payload:
        return None

    tool_name, action = payload.split(".", 1)
    if not tool_name or not action:
        return None

    return tool_name, action


def _stringify_button_value(value: str | dict[str, Any] | None, *, fallback: str) -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def build_tool_button(
    *,
    label: str,
    tool_name: str,
    action: str,
    value: str | dict[str, Any] | None = None,
    style: Literal["primary", "danger"] | None = None,
) -> dict[str, Any]:
    """Build a Slack button element with standardized tool action IDs."""
    action_id = build_tool_action_id(tool_name, action)
    button: dict[str, Any] = {
        "type": "button",
        "text": {"type": "plain_text", "text": label[:75]},
        "action_id": action_id,
        "value": _stringify_button_value(value, fallback=f"{tool_name}:{action}"),
    }
    if style is not None:
        button["style"] = style
    return button


def build_tool_actions_blocks(
    *,
    prompt_text: str,
    buttons: list[dict[str, Any]],
    block_id: str = "adk_tool_actions",
) -> list[dict[str, Any]]:
    """Build section+actions blocks for a Slack interactive tool prompt."""
    action_elements = [button for button in buttons if isinstance(button, dict)][:25]

    blocks: list[dict[str, Any]] = []
    if prompt_text.strip():
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": prompt_text[:3000],
                },
            }
        )

    if action_elements:
        blocks.append(
            {
                "type": "actions",
                "block_id": block_id[:255],
                "elements": action_elements,
            }
        )

    return blocks
