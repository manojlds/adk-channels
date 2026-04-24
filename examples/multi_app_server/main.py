"""Multi-app FastAPI server with ADK Channels.

This example shows how to run multiple ADK agents alongside channel adapters
(Slack, Telegram, Webhooks) in a single FastAPI application.

Architecture:
-------------
FastAPI App
├── / (ADK agent endpoints - if mounted)
├── /channels/webhook/{adapter}  (Webhook receivers for external platforms)
├── /channels/health             (Channels healthcheck)
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
    export ADK_CHANNELS_BRIDGE__ENABLED=true

    uv run python examples/multi_app_server/main.py

Then in Slack:
    @YourBot in #support -> routed to support agent
    @YourBot in #engineering -> routed to engineering agent
"""

from __future__ import annotations

import logging
import os

from google.adk.agents import Agent
from google.adk.sessions import InMemorySessionService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("multi_app_server")


def create_support_agent() -> Agent:
    """Support agent for customer-facing queries."""
    return Agent(
        model="gemini-2.0-flash",
        name="support_bot",
        description="Customer support assistant",
        instruction="""
You are a friendly customer support assistant. Help users with:
- Product questions
- Troubleshooting
- Account issues
- Billing inquiries

Be empathetic, concise, and actionable. If you need more info, ask follow-up questions.
Use Slack markdown for formatting.
        """,
    )


def create_engineering_agent() -> Agent:
    """Engineering agent for technical queries."""
    return Agent(
        model="gemini-2.0-flash",
        name="engineering_bot",
        description="Engineering assistant",
        instruction="""
You are an engineering assistant. Help with:
- Code review and debugging
- Architecture decisions
- Technical documentation
- Best practices

Be precise, include code examples where helpful, and cite sources when possible.
Use Slack markdown (code blocks, bullets) for formatting.
        """,
    )


def create_default_agent() -> Agent:
    """Default agent for general queries."""
    return Agent(
        model="gemini-2.0-flash",
        name="general_bot",
        description="General assistant",
        instruction="""
You are a helpful general-purpose assistant. Route or handle queries appropriately.
If a question seems support-related, suggest contacting #support.
If engineering-related, suggest #engineering.
        """,
    )


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

    from adk_channels import ChannelRegistry, ChannelsConfig
    from adk_channels.multi_app_bridge import MultiAppBridge
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
            )
        else:
            logger.error("Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN env vars")
            return

    config.bridge.enabled = True

    # Create registry
    registry = ChannelRegistry()
    # Note: load_config requires async, so we do it in startup instead

    # Create bridge with multi-app routing
    bridge = MultiAppBridge(
        bridge_config=config.bridge,
        registry=registry,
        app_resolver=app_resolver,
        agent_factories={
            "support": create_support_agent,
            "engineering": create_engineering_agent,
            "default": create_default_agent,
        },
        session_service_factory=InMemorySessionService,
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
    logger.info("Channels webhook endpoint: http://0.0.0.0:8000/channels/webhook/{adapter}")
    logger.info("Channels health: http://0.0.0.0:8000/channels/health")

    uvicorn.run(fastapi_app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
