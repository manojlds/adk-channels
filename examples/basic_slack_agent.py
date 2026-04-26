"""Minimal Slack + ADK single-agent setup (basic path)."""

from __future__ import annotations

import asyncio
import logging
import os

from google.adk.agents import Agent

from adk_channels import ChannelRegistry, ChannelsConfig, ChatBridge

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("basic_slack_agent")


def create_agent() -> Agent:
    """Create a basic ADK assistant for Slack."""
    model = os.environ.get("MODEL", "gemini-2.0-flash")
    return Agent(
        model=model,
        name="basic_slack_assistant",
        description="Minimal Slack assistant",
        instruction="You are a helpful Slack assistant. Keep answers concise and clear.",
    )


async def main() -> None:
    """Run the minimal Slack agent with ChatBridge."""
    config = ChannelsConfig()

    if "slack" not in config.adapters:
        bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        app_token = os.environ.get("SLACK_APP_TOKEN", "")
        if not bot_token or not app_token:
            logger.error(
                "Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN, or configure ADK_CHANNELS_ADAPTERS__SLACK__* env vars"
            )
            return

        from adk_channels.config import AdapterConfig

        config.adapters["slack"] = AdapterConfig(
            type="slack",
            bot_token=bot_token,
            app_token=app_token,
        )

    config.bridge.enabled = True

    registry = ChannelRegistry()
    await registry.load_config(config)

    bridge = ChatBridge(
        bridge_config=config.bridge,
        registry=registry,
        agent_factory=create_agent,
    )
    bridge.start()

    registry.set_on_incoming(bridge.handle_message)
    await registry.start_listening()

    logger.info("Basic Slack agent is running. Press Ctrl+C to stop.")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        bridge.stop()
        await registry.stop_all()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
