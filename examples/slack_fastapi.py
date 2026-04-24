"""FastAPI + Slack Agent — Ready-to-run example.

This is the simplest way to get an ADK agent talking to Slack via a FastAPI server.

Usage:
    # 1. Set env vars (see SLACK_SETUP.md)
    export SLACK_BOT_TOKEN=xoxb-...
    export SLACK_APP_TOKEN=xapp-...
    export GOOGLE_API_KEY=...  # or GOOGLE_GENAI_API_KEY

    # 2. Run the server
    uv run python examples/slack_fastapi.py

    # Or via the installed script:
    uv run adk-channels-slack

Then in Slack:
    - DM the bot directly
    - @mention the bot in a channel
    - Use /adk <message> slash command
"""

from __future__ import annotations

import logging
import os

import uvicorn
from fastapi import FastAPI
from google.adk.agents import Agent

from adk_channels import ChannelRegistry, ChannelsConfig
from adk_channels.bridge import ChatBridge
from adk_channels.config import AdapterConfig
from adk_channels.server_integration import ChannelsFastAPIIntegration

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("slack_fastapi")


def create_agent() -> Agent:
    """Create a simple ADK agent."""
    return Agent(
        model="gemini-2.0-flash",
        name="slack_assistant",
        description="A helpful assistant accessible via Slack",
        instruction="""
You are a helpful AI assistant integrated into Slack. You help users with:
- Answering questions
- Writing and reviewing code
- Summarizing information
- General productivity tasks

Keep responses concise and well-formatted for Slack (use markdown).
If you need more context, ask follow-up questions.
        """,
    )


def main() -> None:
    """Run the FastAPI server with Slack integration."""
    # Load config from env vars
    config = ChannelsConfig()

    # Ensure Slack adapter is configured
    if "slack" not in config.adapters:
        bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        app_token = os.environ.get("SLACK_APP_TOKEN", "")
        if not bot_token or not app_token:
            logger.error(
                "Missing Slack tokens. Set these env vars:\n"
                "  export SLACK_BOT_TOKEN=xoxb-your-bot-token\n"
                "  export SLACK_APP_TOKEN=xapp-your-app-token\n"
                "\nSee SLACK_SETUP.md for how to create a Slack app."
            )
            raise SystemExit(1)

        config.adapters["slack"] = AdapterConfig(
            type="slack",
            bot_token=bot_token,
            app_token=app_token,
        )

    config.bridge.enabled = True

    # Create registry and bridge
    registry = ChannelRegistry()
    bridge = ChatBridge(
        bridge_config=config.bridge,
        registry=registry,
        agent_factory=create_agent,
    )

    # FastAPI app
    fastapi_app = FastAPI(
        title="ADK Slack Agent",
        description="Google ADK agent connected to Slack via adk-channels",
    )

    # Integrate channels into FastAPI
    integration = ChannelsFastAPIIntegration(
        fastapi_app=fastapi_app,
        registry=registry,
        bridge=bridge,
        config=config,
    )
    integration.setup()

    @fastapi_app.get("/")
    async def root() -> dict[str, str]:
        return {"status": "ok", "service": "adk-slack-agent"}

    logger.info("=" * 60)
    logger.info("ADK Slack Agent Server")
    logger.info("=" * 60)
    logger.info("Health:    http://0.0.0.0:8000/channels/health")
    logger.info("Status:    http://0.0.0.0:8000/channels/status")
    logger.info("Webhooks:  http://0.0.0.0:8000/channels/webhook/{adapter}")
    logger.info("=" * 60)
    logger.info("In Slack: DM the bot, @mention it, or use /adk <msg>")
    logger.info("=" * 60)

    uvicorn.run(fastapi_app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
