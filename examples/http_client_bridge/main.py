"""Multi-app server using HTTP clients to call ADK endpoints internally.

This pattern is useful when your ADK apps are already exposed as FastAPI
endpoints (e.g., using ADK's built-in server utilities) and you want
the channel bridge to call them via HTTP rather than instantiating
Runners directly.

Architecture:
-------------
FastAPI App
├── /agents/support/run          (ADK support agent endpoint)
├── /agents/engineering/run      (ADK engineering agent endpoint)
├── /channels/webhook/{adapter}  (Channel webhook receivers)
└── Background: Slack Socket Mode, Telegram polling

The bridge uses HTTP clients to POST to the ADK endpoints, keeping the
channel layer decoupled from the agent layer.

Usage:
------
    uv run python examples/http_client_bridge/main.py
"""

from __future__ import annotations

import logging
import os
from typing import Any

from google.adk.agents import Agent
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("http_client_bridge")


# --- ADK Agents (mounted as FastAPI endpoints) ---


def create_support_agent() -> Agent:
    return Agent(
        model="gemini-2.0-flash",
        name="support_bot",
        instruction="You are a customer support assistant. Be friendly and helpful.",
    )


def create_engineering_agent() -> Agent:
    return Agent(
        model="gemini-2.0-flash",
        name="engineering_bot",
        instruction="You are an engineering assistant. Be precise and technical.",
    )


# --- HTTP Clients for Bridge ---


class InternalADKClient:
    """HTTP client that calls ADK agent endpoints running in the same process.

    In a real deployment, these could be separate services. Here we simulate
    HTTP calls but actually invoke Runners directly for simplicity.
    """

    def __init__(self, agent: Agent, app_name: str, base_url: str = "") -> None:
        self.agent = agent
        self.app_name = app_name
        self.base_url = base_url
        self.session_service = InMemorySessionService()  # type: ignore[no-untyped-call]

    async def call(self, session_id: str, text: str) -> str:
        """Call the ADK agent and return the response text."""
        from google.adk.runners import Runner

        runner = Runner(
            agent=self.agent,
            app_name=self.app_name,
            session_service=self.session_service,
        )
        message = Content(role="user", parts=[Part(text=text)])

        responses = []
        async for event in runner.run_async(
            user_id=session_id,
            session_id=session_id,
            new_message=message,
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        responses.append(part.text)

        return "\n".join(responses) or "(no response)"


# --- App Resolver ---

CHANNEL_MAP = {
    "C0SUPPORT123": "support",
    "C0ENG123456": "engineering",
}


def app_resolver(message) -> str:
    sender = message.sender
    channel_id = sender.split(":")[0] if ":" in sender else sender
    return CHANNEL_MAP.get(channel_id, "default")


# --- Main ---


def main() -> None:
    import uvicorn
    from fastapi import FastAPI

    from adk_channels import ChannelRegistry, ChannelsConfig
    from adk_channels.multi_app_bridge import MultiAppBridge
    from adk_channels.server_integration import ChannelsFastAPIIntegration

    config = ChannelsConfig()

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
            logger.error("Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN")
            return

    config.bridge.enabled = True

    # Create agents
    support_agent = create_support_agent()
    eng_agent = create_engineering_agent()

    # Create HTTP clients
    support_client = InternalADKClient(support_agent, "support")
    eng_client = InternalADKClient(eng_agent, "engineering")

    # Create registry and bridge
    registry = ChannelRegistry()
    bridge = MultiAppBridge(
        bridge_config=config.bridge,
        registry=registry,
        app_resolver=app_resolver,
        http_clients={
            "support": support_client.call,
            "engineering": eng_client.call,
        },
    )

    # FastAPI app
    fastapi_app = FastAPI(title="ADK HTTP Client Bridge Server")

    # Mount ADK agent endpoints (simulated - in production these might be separate services)
    @fastapi_app.post("/agents/{app_name}/run")
    async def run_agent(app_name: str, request: dict[str, Any]) -> dict[str, Any]:
        """ADK-style run endpoint."""
        session_id = request.get("session_id", "default")
        text = request.get("text", "")

        if app_name == "support":
            response = await support_client.call(session_id, text)
        elif app_name == "engineering":
            response = await eng_client.call(session_id, text)
        else:
            response = "Unknown app"

        return {"response": response, "app": app_name}

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
        return {"status": "ok", "mode": "http-client-bridge"}

    logger.info("Starting server on http://0.0.0.0:8000")
    logger.info("ADK endpoints: POST /agents/{support,engineering}/run")
    logger.info("Channels webhook: POST /channels/webhook/{adapter}")

    uvicorn.run(fastapi_app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
