"""Example ADK agent with Slack integration via adk-channels."""

from __future__ import annotations

# ruff: noqa: E402, I001

import asyncio
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from google.adk.agents import Agent

from adk_channels import ChannelRegistry, ChannelsConfig, ChatBridge
from examples.agents import create_interactive_files_agent, create_tool_action_router, resolve_model
from examples.session_service import create_sqlite_session_service, resolve_session_db_path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("slack_agent")


def create_agent() -> Agent:
    """Create the shared tool-enabled agent used across examples."""
    model = resolve_model(logger=logger)
    return create_interactive_files_agent(model=model)


async def run_bridge() -> None:
    """Run the chat bridge with Slack."""
    # Configuration can come from env vars or a file
    # Env vars:
    #   ADK_CHANNELS_ADAPTERS__SLACK__TYPE=slack
    #   ADK_CHANNELS_ADAPTERS__SLACK__BOT_TOKEN=xoxb-...
    #   ADK_CHANNELS_ADAPTERS__SLACK__APP_TOKEN=xapp-...

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
                respond_to_mentions_only=True,
            )
        else:
            logger.error(
                "No Slack configuration found. Set env vars:\n"
                "  ADK_CHANNELS_ADAPTERS__SLACK__BOT_TOKEN=xoxb-...\n"
                "  ADK_CHANNELS_ADAPTERS__SLACK__APP_TOKEN=xapp-..."
            )
            return

    # Set up registry
    registry = ChannelRegistry()
    await registry.load_config(config)

    errors = registry.get_errors()
    for err in errors:
        logger.warning("Adapter error: %s - %s", err["adapter"], err["error"])

    # Create ADK agent components
    agent = create_agent()

    # Bridge with ADK integration
    interaction_router = create_tool_action_router()

    bridge = ChatBridge(
        bridge_config=config.bridge,
        registry=registry,
        agent_factory=lambda: agent,
        interaction_handler=interaction_router,
        session_service_factory=create_sqlite_session_service,
    )
    bridge.start()

    # Set up incoming message handler
    async def on_message(message) -> None:
        await bridge.handle_message(message)

    registry.set_on_incoming(on_message)
    await registry.start_listening()

    logger.info("Slack agent is running. Press Ctrl+C to stop.")
    logger.info("ADK sessions: %s", resolve_session_db_path())

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
