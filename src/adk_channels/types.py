"""Shared types for adk-channels."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class AdapterDirection(str, Enum):
    """Directionality of an adapter."""

    INCOMING = "incoming"
    OUTGOING = "outgoing"
    BIDIRECTIONAL = "bidirectional"


@dataclass
class IncomingAttachment:
    """File attachment on an incoming message."""

    type: Literal["image", "document", "audio"]
    path: str
    filename: str | None = None
    mime_type: str | None = None
    size: int | None = None


@dataclass
class IncomingMessage:
    """Message received from an external channel."""

    adapter: str
    sender: str
    text: str
    attachments: list[IncomingAttachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelMessage:
    """Message to send out via a channel."""

    adapter: str
    recipient: str
    text: str | None = None
    source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# Callback type for incoming messages
OnIncomingMessage = Callable[[IncomingMessage], Awaitable[None] | None]


@dataclass
class QueuedPrompt:
    """A queued prompt waiting to be processed by the bridge."""

    id: str
    adapter: str
    sender: str
    text: str
    attachments: list[IncomingAttachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    enqueued_at: float = 0.0


@dataclass
class SenderSession:
    """Per-sender session state."""

    adapter: str
    sender: str
    display_name: str
    queue: list[QueuedPrompt] = field(default_factory=list)
    processing: bool = False
    abort_controller: Any | None = None
    message_count: int = 0
    started_at: float = 0.0
    last_activity_at: float = 0.0


@dataclass
class RunResult:
    """Result from running an agent prompt."""

    ok: bool
    response: str
    thoughts: list[str] = field(default_factory=list)
    tool_interactions: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    duration_ms: float = 0.0
    exit_code: int = 0
