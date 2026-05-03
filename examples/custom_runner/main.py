"""Example using a custom agent runner instead of ADK."""

from __future__ import annotations

import asyncio
import logging
import os

from adk_channels import ChannelRegistry, ChannelsConfig, ChatBridge

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("custom_runner")


async def custom_agent_runner(session_id: str, text: str) -> str:
    """Custom agent logic — replace with your own LLM call."""
    # This is where you'd call your LLM, RAG pipeline, etc.
    return f"You said: {text}\nSession: {session_id}"


async def main() -> None:
    config = ChannelsConfig()

    # Ensure Slack adapter is configured
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

    registry = ChannelRegistry()
    await registry.load_config(config)

    bridge = ChatBridge(
        bridge_config=config.bridge,
        registry=registry,
        agent_runner=custom_agent_runner,
    )
    bridge.start()

    registry.set_on_incoming(bridge.handle_message)
    await registry.start_listening()

    logger.info("Custom agent is running on Slack. Press Ctrl+C to stop.")
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        bridge.stop()
        await registry.stop_all()


if __name__ == "__main__":
    asyncio.run(main())
