"""Example ADK agent with Slack integration via adk-channels."""

from __future__ import annotations

import asyncio
import logging
import os

from google.adk.agents import Agent

from adk_channels import ChannelRegistry, ChannelsConfig, ChatBridge

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("slack_agent")


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


async def run_bridge() -> None:
    """Run the chat bridge with Slack."""
    # Configuration can come from env vars or a file
    # Env vars:
    #   ADK_CHANNELS_ADAPTERS__SLACK__TYPE=slack
    #   ADK_CHANNELS_ADAPTERS__SLACK__BOT_TOKEN=xoxb-...
    #   ADK_CHANNELS_ADAPTERS__SLACK__APP_TOKEN=xapp-...
    #   ADK_CHANNELS_BRIDGE__ENABLED=true

    config = ChannelsConfig()

    # Ensure Slack adapter is configured
    if "slack" not in config.adapters:
        # Try to load from explicit env vars
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
            logger.error(
                "No Slack configuration found. Set env vars:\n"
                "  ADK_CHANNELS_ADAPTERS__SLACK__BOT_TOKEN=xoxb-...\n"
                "  ADK_CHANNELS_ADAPTERS__SLACK__APP_TOKEN=xapp-..."
            )
            return

    config.bridge.enabled = True

    # Set up registry
    registry = ChannelRegistry()
    await registry.load_config(config)

    errors = registry.get_errors()
    for err in errors:
        logger.warning("Adapter error: %s - %s", err["adapter"], err["error"])

    # Create ADK agent components
    agent = create_agent()

    # Bridge with ADK integration
    bridge = ChatBridge(
        bridge_config=config.bridge,
        registry=registry,
        agent_factory=lambda: agent,
    )
    bridge.start()

    # Set up incoming message handler
    async def on_message(message) -> None:
        await bridge.handle_message(message)

    registry.set_on_incoming(on_message)
    await registry.start_listening()

    logger.info("Slack agent is running. Press Ctrl+C to stop.")

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
    asyncio.run(run_bridge())
