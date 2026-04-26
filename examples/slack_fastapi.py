"""FastAPI + Slack agent with tool-driven interactive workflows.

This example supports any OpenAI-compatible model via LiteLLM or direct
OpenAI-compatible endpoints (for example opencode.ai), and demonstrates
tool-native Slack interactions:
- Approval flow for delete requests
- Multi-option selection flow

Usage:
    # 1. Set tokens in .env file (see SLACK_SETUP.md)
    # 2. Run the server — .env is loaded automatically
    uv run python examples/slack_fastapi.py

    # Or via the installed script:
    uv run adk-channels-slack
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

from adk_channels import ChannelRegistry, ChannelsConfig
from adk_channels.bridge import ChatBridge
from adk_channels.config import AdapterConfig
from adk_channels.server_integration import ChannelsFastAPIIntegration
from examples.agents import (
    create_interactive_files_agent,
    create_tool_action_router,
    resolve_model,
)

# Load .env from project root (one level up from this file)
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("slack_fastapi")


def main() -> None:
    """Run the FastAPI server with Slack integration."""
    config = ChannelsConfig()

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
    model = resolve_model(logger=logger)

    interaction_router = create_tool_action_router()

    registry = ChannelRegistry()
    bridge = ChatBridge(
        bridge_config=config.bridge,
        registry=registry,
        agent_factory=lambda: create_interactive_files_agent(model=model),
        interaction_handler=interaction_router,
    )

    fastapi_app = FastAPI(
        title="ADK Slack Agent",
        description="Google ADK agent connected to Slack via adk-channels",
    )

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
    logger.info("Model:     %s", model)
    logger.info("Health:    http://0.0.0.0:8000/channels/health")
    logger.info("Status:    http://0.0.0.0:8000/channels/status")
    logger.info("=" * 60)
    logger.info("In Slack: DM the bot, @mention it, or use /adk <msg>")
    logger.info("Try tool flows:")
    logger.info("  - list internal files")
    logger.info("  - delete deployment-plan.md")
    logger.info("  - choose multiple files for cleanup")
    logger.info("=" * 60)

    uvicorn.run(fastapi_app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
