"""FastAPI server integration for adk-channels.

This module provides utilities to integrate adk-channels with ADK's FastAPI
deployment pattern, supporting multiple agent apps running alongside channel
adapters (Slack, Telegram, and outgoing webhooks).

Typical ADK FastAPI Pattern:
---------------------------
ADK apps are often deployed as FastAPI servers where each agent is mounted
as a sub-application or exposed via runner endpoints. This module lets you:

1. Run channel adapters (Slack Socket Mode, Telegram polling) as background tasks
2. Expose channel health/status endpoints
3. Route incoming messages to the correct ADK app/agent
4. Share session services between ADK apps and the channel bridge

Example - Multi-app FastAPI server:
-----------------------------------
    from pathlib import Path

    from fastapi import FastAPI
    from google.adk.agents import Agent
    from google.adk.sessions.sqlite_session_service import SqliteSessionService
    from adk_channels import ChannelsConfig, ChannelRegistry
    from adk_channels.bridge import ChatBridge
    from adk_channels.server_integration import ChannelsFastAPIIntegration

    # Define your ADK agents
    support_agent = Agent(model="gemini-2.0-flash", name="support_bot", ...)
    eng_agent = Agent(model="gemini-2.0-flash", name="eng_bot", ...)

    # Create the main FastAPI app (your ADK server)
    app = FastAPI()

    # Configure channels
    session_db = Path(".adk_channels/sessions.sqlite")
    session_db.parent.mkdir(parents=True, exist_ok=True)
    config = ChannelsConfig()
    registry = ChannelRegistry()
    await registry.load_config(config)

    # Bridge with multi-app routing
    bridge = ChatBridge(
        bridge_config=config.bridge,
        registry=registry,
        app_resolver=lambda msg: "support" if msg.metadata.get("channel_id") == "SUPPORT_CHAN" else "engineering",
        agent_factories={
            "support": lambda: support_agent,
            "engineering": lambda: eng_agent,
        },
        session_service_factory=lambda: SqliteSessionService(str(session_db)),
    )

    # Integrate channels into FastAPI
    integration = ChannelsFastAPIIntegration(
        fastapi_app=app,
        registry=registry,
        bridge=bridge,
    )
    integration.setup()

    # Now your FastAPI app serves:
    # - Your existing ADK endpoints (mount them as usual)
    # - Channel status endpoints at /channels/*
    # - Background tasks for Slack Socket Mode / Telegram polling
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    from fastapi import APIRouter, FastAPI
except ImportError:
    FastAPI = None  # type: ignore
    APIRouter = None  # type: ignore

from adk_channels.config import ChannelsConfig
from adk_channels.registry import ChannelRegistry
from adk_channels.types import IncomingMessage

logger = logging.getLogger("adk_channels.server_integration")


class ChannelsFastAPIIntegration:
    """Integrates adk-channels into a FastAPI application.

    Manages:
    - Background tasks for polling/Socket Mode adapters
    - Health and status routes for channel integration
    - Lifecycle hooks (startup/shutdown)
    - Healthcheck endpoint
    """

    def __init__(
        self,
        fastapi_app: FastAPI,
        registry: ChannelRegistry,
        bridge: Any,
        config: ChannelsConfig | None = None,
        webhook_prefix: str = "/channels",
    ) -> None:
        if FastAPI is None:
            raise ImportError(
                "fastapi is required for server integration. Install: uv pip install adk-channels[webhook]"
            )
        self._app = fastapi_app
        self._registry = registry
        self._bridge = bridge
        self._config = config
        self._webhook_prefix = webhook_prefix
        self._background_tasks: list[asyncio.Task[Any]] = []
        self._setup_done = False

    def setup(self) -> None:
        """Set up routes and event handlers on the FastAPI app."""
        if self._setup_done:
            return
        self._setup_done = True

        # Register lifecycle events
        self._app.add_event_handler("startup", self._on_startup)
        self._app.add_event_handler("shutdown", self._on_shutdown)

        # Create router for channel endpoints
        router = APIRouter(prefix=self._webhook_prefix)

        @router.get("/health")
        async def health() -> dict[str, Any]:
            """Health check for channels."""
            stats = self._bridge.get_stats() if hasattr(self._bridge, "get_stats") else {}
            adapters = self._registry.list_adapters()
            return {
                "status": "ok",
                "bridge": stats,
                "adapters": adapters,
            }

        @router.get("/status")
        async def status() -> dict[str, Any]:
            """Detailed status of channels."""
            errors = self._registry.get_errors()
            return {
                "status": "ok",
                "errors": errors,
                "bridge": self._bridge.get_stats() if hasattr(self._bridge, "get_stats") else {},
            }

        self._app.include_router(router)
        logger.info("Channels routes mounted at %s", self._webhook_prefix)

    async def _on_startup(self) -> None:
        """Start channel adapters and bridge on FastAPI startup."""
        logger.info("Starting channel adapters...")

        # Load config if provided
        if self._config:
            await self._registry.load_config(self._config)
            errors = self._registry.get_errors()
            for err in errors:
                logger.warning("Adapter error: %s - %s", err["adapter"], err["error"])

        # Set up incoming message handler
        async def on_message(message: IncomingMessage) -> None:
            await self._bridge.handle_message(message)

        self._registry.set_on_incoming(on_message)

        # Start bridge
        if hasattr(self._bridge, "start"):
            self._bridge.start()

        # Start listening adapters (Slack Socket Mode, Telegram polling)
        # This is non-blocking for Socket Mode (runs its own event loop)
        # and blocking for Telegram (runs its own polling loop)
        # We run them in background tasks
        task = asyncio.create_task(self._registry.start_listening())
        self._background_tasks.append(task)

        logger.info("Channel adapters started")

    async def _on_shutdown(self) -> None:
        """Stop channel adapters and bridge on FastAPI shutdown."""
        logger.info("Shutting down channel adapters...")

        # Stop bridge
        if hasattr(self._bridge, "stop"):
            self._bridge.stop()

        # Stop registry
        await self._registry.stop_all()

        # Cancel background tasks
        for task in self._background_tasks:
            task.cancel()
            with __import__("contextlib").suppress(asyncio.CancelledError):
                await task
        self._background_tasks.clear()

        logger.info("Channel adapters stopped")


def create_fastapi_app(
    agents: dict[str, Any],
    config: ChannelsConfig | None = None,
    app_resolver: Any | None = None,
    session_service_factory: Any | None = None,
    interaction_handler: Any | None = None,
    webhook_prefix: str = "/channels",
) -> FastAPI:
    """Create a complete FastAPI app with ADK channels integration.

    This is a convenience factory for the common case where you want a
    single FastAPI app with multiple ADK agents and channel support.

    Args:
        agents: Dict of app_name -> ADK Agent instances or factories.
        config: ChannelsConfig. If None, loaded from env vars.
        app_resolver: Callable to resolve IncomingMessage -> app_name.
                      Defaults to always "default".
        session_service_factory: Optional shared SessionService factory.
        interaction_handler: Optional callable to handle interactive messages
                             before agent execution (for example ToolActionRouter).
        webhook_prefix: Route prefix for channel webhooks.

    Returns:
        A FastAPI application with channels integrated.

    Example:
        app = create_fastapi_app(
            agents={
                "support": Agent(model="gemini-2.0-flash", name="support_bot", ...),
                "engineering": Agent(model="gemini-2.0-flash", name="eng_bot", ...),
            },
            app_resolver=lambda msg: "support" if "support" in msg.text else "engineering",
        )
    """
    try:
        from fastapi import FastAPI as _FastAPI
    except ImportError as err:
        raise ImportError("fastapi is required. Install: uv pip install adk-channels[webhook]") from err

    from adk_channels.bridge import ChatBridge

    cfg = config or ChannelsConfig()
    registry = ChannelRegistry()

    # Convert agents to factories if they are instances
    agent_factories: dict[str, Any] = {}
    for name, agent in agents.items():
        if callable(agent) and not hasattr(agent, "model"):
            # It's already a factory
            agent_factories[name] = agent
        else:
            # It's an instance, wrap in factory
            agent_factories[name] = lambda a=agent: a

    bridge = ChatBridge(
        bridge_config=cfg.bridge,
        registry=registry,
        app_resolver=app_resolver,
        agent_factories=agent_factories,
        session_service_factory=session_service_factory,
        interaction_handler=interaction_handler,
    )

    app = _FastAPI(title="ADK Channels Server")
    integration = ChannelsFastAPIIntegration(
        fastapi_app=app,
        registry=registry,
        bridge=bridge,
        config=cfg,
        webhook_prefix=webhook_prefix,
    )
    integration.setup()

    return app
