"""Multi-app FastAPI server with ADK Channels.

This example shows how to run multiple ADK agents alongside channel adapters
(Slack, Telegram, Webhooks) in a single FastAPI application.

Architecture:
-------------
FastAPI App
├── / (ADK agent endpoints - if mounted)
├── /channels/health             (Channels healthcheck)
├── /channels/status             (Channels status)
└── Background tasks: Slack Socket Mode, Telegram polling

Agents:
-------
- "support" agent: Handles support-related queries
- "engineering" agent: Handles engineering/technical queries

Routing:
--------
Messages from #support Slack channel -> support agent
Messages from #engineering Slack channel -> engineering agent
Everything else -> default agent

Usage:
------
    export ADK_CHANNELS_ADAPTERS__SLACK__TYPE=slack
    export ADK_CHANNELS_ADAPTERS__SLACK__BOT_TOKEN=xoxb-...
    export ADK_CHANNELS_ADAPTERS__SLACK__APP_TOKEN=xapp-...

    uv run python examples/multi_app_server/main.py

Then in Slack:
    @YourBot in #support -> routed to support agent
    @YourBot in #engineering -> routed to engineering agent
"""

from __future__ import annotations

# ruff: noqa: E402, I001

import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from examples.agents import create_default_agent, create_engineering_agent, create_support_agent, resolve_model
from examples.session_service import create_sqlite_session_service, resolve_session_db_path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("multi_app_server")

# Channel ID to app mapping
# In production, load this from config or database
CHANNEL_MAP = {
    "C0SUPPORT123": "support",  # #support channel
    "C0ENG123456": "engineering",  # #engineering channel
}


def app_resolver(message) -> str:
    """Resolve incoming message to the right app/agent.

    This function is called for every incoming message. It can inspect:
    - message.adapter (slack, telegram, webhook)
    - message.sender (user/channel ID)
    - message.metadata (channel_id, user_id, event_type, etc.)
    - message.text (the message content)
    """
    # For Slack, the sender is channel_id:thread_ts or channel_id
    sender = message.sender
    channel_id = sender.split(":")[0] if ":" in sender else sender

    # Check channel mapping
    if channel_id in CHANNEL_MAP:
        return CHANNEL_MAP[channel_id]

    # For Telegram, you might route based on chat ID
    # if message.adapter == "telegram" and message.sender == "123456789":
    #     return "support"

    # Default fallback
    return "default"


def main() -> None:
    """Create and run the multi-app FastAPI server."""
    import uvicorn
    from fastapi import FastAPI

    from adk_channels import ChannelRegistry, ChannelsConfig, ChatBridge
    from adk_channels.server_integration import ChannelsFastAPIIntegration

    # Load config from env vars
    config = ChannelsConfig()

    # Ensure Slack is configured
    if "slack" not in config.adapters:
        bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        app_token = os.environ.get("SLACK_APP_TOKEN", "")
        if bot_token and app_token:
            from adk_channels.config import AdapterConfig

            config.adapters["slack"] = AdapterConfig(
                type="slack",
                bot_token=bot_token,
                app_token=app_token,
                respond_to_mentions_only=True,
            )
        else:
            logger.error("Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN env vars")
            return

    model = resolve_model(logger=logger)

    # Create registry
    registry = ChannelRegistry()
    # Note: load_config requires async, so we do it in startup instead

    # Create bridge with multi-app routing
    bridge = ChatBridge(
        bridge_config=config.bridge,
        registry=registry,
        app_resolver=app_resolver,
        agent_factories={
            "support": lambda: create_support_agent(model=model),
            "engineering": lambda: create_engineering_agent(model=model),
            "default": lambda: create_default_agent(model=model),
        },
        session_service_factory=create_sqlite_session_service,
    )

    # Create FastAPI app
    fastapi_app = FastAPI(title="ADK Multi-App Server")

    # Mount ADK agent endpoints if you have them
    # from google.adk.server import FastAPIAgentServer
    # support_server = FastAPIAgentServer(agent=create_support_agent())
    # fastapi_app.mount("/agents/support", support_server.app)

    # Integrate channels
    integration = ChannelsFastAPIIntegration(
        fastapi_app=fastapi_app,
        registry=registry,
        bridge=bridge,
        config=config,
    )
    integration.setup()

    @fastapi_app.get("/")
    async def root() -> dict[str, str]:
        return {"status": "ok", "service": "adk-multi-app-server"}

    logger.info("Starting server on http://0.0.0.0:8000")
    logger.info("ADK sessions: %s", resolve_session_db_path())
    logger.info("Channels health: http://0.0.0.0:8000/channels/health")
    logger.info("Channels status: http://0.0.0.0:8000/channels/status")

    uvicorn.run(fastapi_app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
