"""Base adapter interface for adk-channels."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from adk_channels.types import AdapterDirection, ChannelMessage, OnIncomingMessage


class BaseChannelAdapter(ABC):
    """Abstract base class for all channel adapters."""

    direction: AdapterDirection = AdapterDirection.BIDIRECTIONAL

    @abstractmethod
    async def send(self, message: ChannelMessage) -> None:
        """Send a message outward. Required for outgoing/bidirectional adapters."""
        ...

    @abstractmethod
    async def start(self, on_message: OnIncomingMessage) -> None:
        """Start listening for incoming messages. Required for incoming/bidirectional."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop listening and clean up resources."""
        ...

    async def send_typing(self, recipient: str) -> None:  # noqa: B027
        """Send a typing/processing indicator (optional)."""
        pass

    async def sync_bot_commands(self, commands: list[dict[str, str]]) -> None:  # noqa: B027
        """Sync bot commands with the platform (optional)."""
        pass

    async def healthcheck(self) -> dict[str, Any]:
        """Return adapter health status."""
        return {"status": "ok", "adapter": self.__class__.__name__}
