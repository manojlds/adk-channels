"""Helpers for translating ADK/GenAI events into channel-friendly metadata."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


def _stringify_payload(payload: Any, *, max_length: int = 800) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        text = payload
    else:
        try:
            text = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        except TypeError:
            text = str(payload)
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3]}..."


def extract_tool_interaction(part: Any) -> dict[str, Any] | None:
    """Extract a normalized tool interaction from a GenAI part if present."""
    function_call = getattr(part, "function_call", None)
    if function_call is not None:
        raw_payload = getattr(function_call, "args", None)
        return {
            "type": "tool_call",
            "name": str(getattr(function_call, "name", None) or "tool"),
            "payload": _stringify_payload(raw_payload),
            "raw_payload": raw_payload,
        }

    function_response = getattr(part, "function_response", None)
    if function_response is not None:
        raw_payload = getattr(function_response, "response", None)
        return {
            "type": "tool_result",
            "name": str(getattr(function_response, "name", None) or "tool"),
            "payload": _stringify_payload(raw_payload),
            "raw_payload": raw_payload,
        }

    executable_code = getattr(part, "executable_code", None)
    if executable_code is not None:
        language = str(getattr(executable_code, "language", None) or "")
        language_prefix = f"[{language}] " if language else ""
        return {
            "type": "code",
            "name": "executable_code",
            "payload": f"{language_prefix}{_stringify_payload(getattr(executable_code, 'code', None))}".strip(),
        }

    code_execution_result = getattr(part, "code_execution_result", None)
    if code_execution_result is not None:
        return {
            "type": "code_result",
            "name": "code_execution_result",
            "payload": _stringify_payload(getattr(code_execution_result, "output", None)),
        }

    return None


def collect_part_outputs(parts: Iterable[Any]) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Collect thoughts, response text, and tool interactions from event parts."""
    thoughts: list[str] = []
    responses: list[str] = []
    tool_interactions: list[dict[str, Any]] = []

    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            if bool(getattr(part, "thought", False)):
                thoughts.append(text)
            else:
                responses.append(text)

        interaction = extract_tool_interaction(part)
        if interaction is not None:
            tool_interactions.append(interaction)

    return thoughts, responses, tool_interactions


def fallback_response_from_tool_interactions(tool_interactions: Iterable[dict[str, Any]]) -> str | None:
    """Return a human-readable fallback text from structured tool payloads."""
    for interaction in reversed(list(tool_interactions)):
        raw_payload = interaction.get("raw_payload")
        if not isinstance(raw_payload, dict):
            continue

        message = raw_payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()

        text = raw_payload.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

        slack_payload = raw_payload.get("slack")
        if isinstance(slack_payload, dict):
            slack_message = slack_payload.get("message")
            if isinstance(slack_message, str) and slack_message.strip():
                return slack_message.strip()

    return None
