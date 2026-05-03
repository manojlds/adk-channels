"""Working Slack bot — connects directly to Slack and routes through ADK."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents import Agent

from adk_channels import ChannelRegistry, ChannelsConfig
from adk_channels.bridge import ChatBridge
from adk_channels.config import AdapterConfig

# Load .env
load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("slack_bot")


def create_agent() -> Agent:
    model = os.environ.get("MODEL", "gemini-2.0-flash")
    openai_key = os.environ.get("OPENAI_API_KEY")
    openai_base = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")

    if openai_key and openai_base and "gemini" not in model.lower():
        os.environ.setdefault("OPENAI_API_KEY", openai_key)
        os.environ.setdefault("OPENAI_BASE_URL", openai_base)
        logger.info("Using OpenAI model: %s", model)
    else:
        logger.info("Using model: %s", model)

    return Agent(
        model=model,
        name="slack_assistant",
        instruction="You are a helpful AI assistant in Slack. Keep responses concise and use markdown.",
    )


async def main() -> None:
    config = ChannelsConfig()

    if "slack" not in config.adapters:
        bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        app_token = os.environ.get("SLACK_APP_TOKEN", "")
        if not bot_token or not app_token:
            logger.error("Missing Slack tokens in .env")
            return
        config.adapters["slack"] = AdapterConfig(
            type="slack",
            bot_token=bot_token,
            app_token=app_token,
        )

    registry = ChannelRegistry()
    await registry.load_config(config)

    bridge = ChatBridge(
        bridge_config=config.bridge,
        registry=registry,
        agent_factory=create_agent,
    )
    bridge.start()

    async def on_message(message):
        await bridge.handle_message(message)

    registry.set_on_incoming(on_message)
    await registry.start_listening()

    logger.info("=" * 60)
    logger.info("Bot is running! Send a DM or @mention in Slack.")
    logger.info("=" * 60)

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        bridge.stop()
        await registry.stop_all()


if __name__ == "__main__":
    asyncio.run(main())
