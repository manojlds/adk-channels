"""Adapter registry and route resolution for adk-channels."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from adk_channels.adapters.base import BaseChannelAdapter
from adk_channels.config import AdapterConfig, ChannelsConfig
from adk_channels.types import (
    AdapterDirection,
    ChannelMessage,
    IncomingMessage,
    OnIncomingMessage,
)

logger = logging.getLogger("adk_channels.registry")

AdapterFactory = Callable[[AdapterConfig], Awaitable[BaseChannelAdapter]]

_builtin_factories: dict[str, AdapterFactory] = {}


def register_adapter_factory(type_name: str, factory: AdapterFactory) -> None:
    """Register a built-in adapter factory."""
    _builtin_factories[type_name] = factory


def _import_builtin_factories() -> None:
    """Lazily import built-in adapters to avoid hard dependencies."""
    try:
        from adk_channels.adapters.slack import create_slack_adapter

        register_adapter_factory("slack", create_slack_adapter)
    except ImportError:
        pass

    try:
        from adk_channels.adapters.telegram import create_telegram_adapter

        register_adapter_factory("telegram", create_telegram_adapter)
    except ImportError:
        pass

    try:
        from adk_channels.adapters.webhook import create_webhook_adapter

        register_adapter_factory("webhook", create_webhook_adapter)
    except ImportError:
        pass


class ChannelRegistry:
    """Registry for channel adapters and routes."""

    def __init__(self) -> None:
        self._adapters: dict[str, BaseChannelAdapter] = {}
        self._routes: dict[str, tuple[str, str]] = {}  # alias -> (adapter, recipient)
        self._errors: list[dict[str, str]] = []
        self._on_incoming: OnIncomingMessage = lambda msg: None
        self._running = False

        if not _builtin_factories:
            _import_builtin_factories()

    def set_on_incoming(self, cb: OnIncomingMessage) -> None:
        """Set the callback for incoming messages."""
        self._on_incoming = cb

    async def load_config(self, config: ChannelsConfig) -> None:
        """Load adapters and routes from config."""
        self._errors = []

        # Stop existing adapters
        await self.stop_all()

        # Preserve custom adapters (prefixed with "custom:")
        custom = {k: v for k, v in self._adapters.items() if k.startswith("custom:")}
        self._adapters = custom

        # Load routes
        self._routes = {}
        for alias, route in config.routes.items():
            self._routes[alias] = (route.adapter, route.recipient)

        # Create adapters from config
        for name, adapter_config in config.adapters.items():
            factory = _builtin_factories.get(adapter_config.type)
            if not factory:
                self._errors.append({"adapter": name, "error": f"Unknown adapter type: {adapter_config.type}"})
                continue
            try:
                adapter = await factory(adapter_config)
                self._adapters[name] = adapter
            except Exception as exc:
                self._errors.append({"adapter": name, "error": str(exc)})
                logger.exception("Failed to create adapter %s", name)

    async def start_listening(self) -> None:
        """Start all incoming/bidirectional adapters."""
        self._running = True
        for name, adapter in self._adapters.items():
            if adapter.direction in (AdapterDirection.INCOMING, AdapterDirection.BIDIRECTIONAL):
                try:
                    await adapter.start(
                        lambda msg, adapter_name=name: self._on_incoming(  # type: ignore[misc]
                            IncomingMessage(
                                adapter=adapter_name,
                                sender=msg.sender,
                                text=msg.text,
                                attachments=msg.attachments,
                                metadata=msg.metadata,
                            )
                        )
                    )
                except Exception as exc:
                    self._errors.append({"adapter": name, "error": f"Failed to start: {exc}"})
                    logger.exception("Failed to start adapter %s", name)

    async def sync_bot_commands(self, commands: list[dict[str, str]]) -> None:
        """Sync bot commands on all adapters that support it."""
        for name, adapter in self._adapters.items():
            if hasattr(adapter, "sync_bot_commands"):
                try:
                    await adapter.sync_bot_commands(commands)
                except Exception as exc:
                    self._errors.append({"adapter": name, "error": f"Failed to sync commands: {exc}"})

    async def stop_all(self) -> None:
        """Stop all adapters."""
        self._running = False
        for adapter in self._adapters.values():
            try:
                await adapter.stop()
            except Exception:
                logger.exception("Error stopping adapter")

    def register(self, name: str, adapter: BaseChannelAdapter) -> None:
        """Register a custom adapter."""
        self._adapters[name] = adapter
        if self._running and adapter.direction in (
            AdapterDirection.INCOMING,
            AdapterDirection.BIDIRECTIONAL,
        ):
            asyncio.create_task(
                adapter.start(
                    lambda msg, adapter_name=name: self._on_incoming(  # type: ignore[misc]
                        IncomingMessage(
                            adapter=adapter_name,
                            sender=msg.sender,
                            text=msg.text,
                            attachments=msg.attachments,
                            metadata=msg.metadata,
                        )
                    )
                )
            )

    def unregister(self, name: str) -> bool:
        """Unregister an adapter."""
        adapter = self._adapters.get(name)
        if adapter:
            asyncio.create_task(adapter.stop())
            return bool(self._adapters.pop(name, None))
        return False

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        """Send a message. Resolves routes and validates adapter supports sending."""
        adapter_name = message.adapter
        recipient = message.recipient

        # Check if this is a route alias
        route = self._routes.get(adapter_name)
        if route:
            adapter_name, route_recipient = route
            if not recipient:
                recipient = route_recipient

        adapter = self._adapters.get(adapter_name)
        if not adapter:
            return {"ok": False, "error": f'No adapter "{adapter_name}"'}

        if adapter.direction == AdapterDirection.INCOMING:
            return {
                "ok": False,
                "error": f'Adapter "{adapter_name}" is incoming-only, cannot send',
            }

        try:
            await adapter.send(
                ChannelMessage(
                    adapter=adapter_name,
                    recipient=recipient,
                    text=message.text,
                    source=message.source,
                    metadata=message.metadata,
                )
            )
            return {"ok": True}
        except Exception as exc:
            logger.exception("Failed to send message via %s", adapter_name)
            return {"ok": False, "error": str(exc)}

    def list_adapters(self) -> list[dict[str, Any]]:
        """List all registered adapters and route aliases."""
        result: list[dict[str, Any]] = []
        for name, adapter in self._adapters.items():
            result.append(
                {
                    "name": name,
                    "type": "adapter",
                    "direction": adapter.direction.value,
                }
            )
        for alias, (adapter_name, recipient) in self._routes.items():
            result.append(
                {
                    "name": alias,
                    "type": "route",
                    "target": f"{adapter_name} -> {recipient}",
                }
            )
        return result

    def get_errors(self) -> list[dict[str, str]]:
        return list(self._errors)

    def get_adapter(self, name: str) -> BaseChannelAdapter | None:
        """Get an adapter by name."""
        return self._adapters.get(name)
