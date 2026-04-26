# ADK Channels Templates

Copy/paste these templates into your own project and adjust names, models, and tool logic.

Before running any template, set Slack credentials:

```bash
export SLACK_BOT_TOKEN=xoxb-your-bot-token
export SLACK_APP_TOKEN=xapp-your-app-token
export ADK_CHANNELS_BRIDGE__ENABLED=true
```

Optional:

```bash
export MODEL=gemini-2.0-flash
```

## 1) Basic Template (Single Agent Chat)

Use this when you want the fastest path to “bot in Slack”.

```python
from __future__ import annotations

import asyncio
import os

from google.adk.agents import Agent

from adk_channels import ChannelRegistry, ChannelsConfig, ChatBridge
from adk_channels.config import AdapterConfig


def create_agent() -> Agent:
    return Agent(
        model=os.environ.get("MODEL", "gemini-2.0-flash"),
        name="basic_slack_assistant",
        instruction="You are a helpful assistant in Slack. Keep responses concise.",
    )


async def main() -> None:
    config = ChannelsConfig()

    # You can also provide this via ADK_CHANNELS_ADAPTERS__SLACK__* env vars.
    if "slack" not in config.adapters:
        config.adapters["slack"] = AdapterConfig(
            type="slack",
            bot_token=os.environ["SLACK_BOT_TOKEN"],
            app_token=os.environ["SLACK_APP_TOKEN"],
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

    print("Basic Slack bot running. Ctrl+C to stop.")
    try:
        while True:
            await asyncio.sleep(1)
    finally:
        bridge.stop()
        await registry.stop_all()


if __name__ == "__main__":
    asyncio.run(main())
```

Run:

```bash
uv run python basic_template.py
```

## 2) Intermediate Template (Tools + Slack Generative UI)

Use this when tools need approvals/selectors and Slack-native interaction controls.

```python
from __future__ import annotations

import asyncio
import os
import time

from google.adk.agents import Agent

from adk_channels import (
    ChannelRegistry,
    ChannelsConfig,
    ChatBridge,
    ToolActionRouter,
    tool_approval,
    tool_info,
    tool_multi_select,
)
from adk_channels.config import AdapterConfig

FILES = {"deployment-plan.md", "incident-playbook.md", "debug.log"}
PENDING_APPROVALS: dict[str, str] = {}


def list_files() -> dict:
    items = sorted(FILES)
    return tool_info("Files: " + ", ".join(items), files=items)


def request_delete_file(file_name: str) -> dict:
    if file_name not in FILES:
        return tool_info(f"File '{file_name}' not found", status="not_found")

    request_id = f"req-{int(time.time() * 1000)}"
    PENDING_APPROVALS[request_id] = file_name

    payload = tool_approval(
        message=f"Approval requested to delete `{file_name}`.",
        tool_name="approval",
        value={"request_id": request_id},
        actions_text=f"Approve deleting `{file_name}`?",
        block_id=f"approval_{request_id}",
    )
    payload["request_id"] = request_id
    payload["file_name"] = file_name
    return payload


def apply_delete_approval(request_id: str, decision: str) -> dict:
    file_name = PENDING_APPROVALS.pop(request_id, None)
    if file_name is None:
        return tool_info("Approval request expired or unknown", status="expired")

    if decision == "approve":
        FILES.discard(file_name)
        return tool_info(f"Approved. Deleted `{file_name}`.", files=sorted(FILES), status="approved")

    return tool_info(f"Rejected. Kept `{file_name}`.", files=sorted(FILES), status="rejected")


def request_targets() -> dict:
    return tool_multi_select(
        message="Waiting for selection.",
        tool_name="targets",
        action="choose",
        options=sorted(FILES),
        placeholder="Select one or more files",
        max_selected_items=3,
        actions_text="Choose files for a follow-up operation.",
        block_id="file_targets",
    )


def apply_targets(selection_csv: str) -> dict:
    selected = [value.strip() for value in selection_csv.split(",") if value.strip()]
    selected = [value for value in selected if value in FILES]
    if not selected:
        return tool_info("No valid files selected", status="empty")
    return tool_info("Selected files: " + ", ".join(selected), selected_files=selected)


def create_agent() -> Agent:
    return Agent(
        model=os.environ.get("MODEL", "gemini-2.0-flash"),
        name="interactive_slack_assistant",
        tools=[
            list_files,
            request_delete_file,
            apply_delete_approval,
            request_targets,
            apply_targets,
        ],
        instruction="""
You manage internal files with tools.

Rules:
1) For listing requests, call list_files.
2) For delete requests, call request_delete_file first; do not claim deletion before approval.
3) For selection requests, call request_targets.
4) Keep answers short and Slack-friendly.
        """,
    )


def create_router() -> ToolActionRouter:
    router = ToolActionRouter()

    @router.on_tool("approval", "approve")
    @router.on_tool("approval", "reject")
    def handle_approval(ctx):
        payload = ctx.action_value_json()
        request_id = str(payload.get("request_id") or ctx.action_value)
        decision = str(ctx.tool_action or "reject")
        result = apply_delete_approval(request_id, decision)
        return str(result.get("message") or "Approval updated")

    @router.on_tool("targets", "choose")
    def handle_targets(ctx):
        result = apply_targets(ctx.action_value)
        return str(result.get("message") or "Selection updated")

    return router


async def main() -> None:
    config = ChannelsConfig()
    if "slack" not in config.adapters:
        config.adapters["slack"] = AdapterConfig(
            type="slack",
            bot_token=os.environ["SLACK_BOT_TOKEN"],
            app_token=os.environ["SLACK_APP_TOKEN"],
        )

    config.bridge.enabled = True

    registry = ChannelRegistry()
    await registry.load_config(config)

    bridge = ChatBridge(
        bridge_config=config.bridge,
        registry=registry,
        agent_factory=create_agent,
        interaction_handler=create_router(),
    )
    bridge.start()

    registry.set_on_incoming(bridge.handle_message)
    await registry.start_listening()

    print("Interactive Slack bot running. Ctrl+C to stop.")
    try:
        while True:
            await asyncio.sleep(1)
    finally:
        bridge.stop()
        await registry.stop_all()


if __name__ == "__main__":
    asyncio.run(main())
```

Run:

```bash
uv run python intermediate_template.py
```

## 3) Advanced Template (FastAPI + Multi-App + Interaction Routing)

Use this when you need multiple specialized agents behind one channel layer.

```python
from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from google.adk.agents import Agent
from google.adk.sessions.sqlite_session_service import SqliteSessionService

from adk_channels import ChannelRegistry, ChannelsConfig, ChatBridge, ToolActionRouter
from adk_channels.config import AdapterConfig
from adk_channels.server_integration import ChannelsFastAPIIntegration


def support_agent() -> Agent:
    return Agent(
        model=os.environ.get("MODEL", "gemini-2.0-flash"),
        name="support_bot",
        instruction="You are a support assistant. Be concise and actionable.",
    )


def engineering_agent() -> Agent:
    return Agent(
        model=os.environ.get("MODEL", "gemini-2.0-flash"),
        name="engineering_bot",
        instruction="You are an engineering assistant. Be technical and precise.",
    )


def default_agent() -> Agent:
    return Agent(
        model=os.environ.get("MODEL", "gemini-2.0-flash"),
        name="default_bot",
        instruction="You are a general assistant.",
    )


CHANNEL_MAP = {
    "C0SUPPORT123": "support",
    "C0ENG123456": "engineering",
}
SESSION_DB = Path(os.environ.get("ADK_CHANNELS_SESSION_DB", ".adk_channels/sessions.sqlite"))


def create_session_service() -> SqliteSessionService:
    SESSION_DB.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSessionService(str(SESSION_DB))


def app_resolver(message) -> str:
    channel_id = str(message.metadata.get("channel_id") or message.sender.split(":")[0])
    return CHANNEL_MAP.get(channel_id, "default")


def create_router() -> ToolActionRouter:
    router = ToolActionRouter()

    @router.on_tool("approval", "approve")
    @router.on_tool("approval", "reject")
    def handle_approval(ctx):
        decision = ctx.tool_action or "reject"
        request_id = ctx.action_value_json().get("request_id", "unknown")
        return f"[{ctx.message.adapter}] recorded {decision} for {request_id}"

    return router


def main() -> None:
    app = FastAPI(title="Advanced ADK Channels Server")
    config = ChannelsConfig()

    if "slack" not in config.adapters:
        config.adapters["slack"] = AdapterConfig(
            type="slack",
            bot_token=os.environ["SLACK_BOT_TOKEN"],
            app_token=os.environ["SLACK_APP_TOKEN"],
        )

    config.bridge.enabled = True

    registry = ChannelRegistry()
    bridge = ChatBridge(
        bridge_config=config.bridge,
        registry=registry,
        app_resolver=app_resolver,
        agent_factories={
            "support": support_agent,
            "engineering": engineering_agent,
            "default": default_agent,
        },
        session_service_factory=create_session_service,
        interaction_handler=create_router(),
    )

    integration = ChannelsFastAPIIntegration(
        fastapi_app=app,
        registry=registry,
        bridge=bridge,
        config=config,
    )
    integration.setup()

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"status": "ok", "service": "advanced-adk-channels"}

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
```

Run:

```bash
uv run python advanced_template.py
```

## Which Template Should You Start With?

- Start with **Basic** if you are validating channel connectivity.
- Move to **Intermediate** when your tools need approval/select flows.
- Use **Advanced** for production FastAPI deployments and domain-based agent routing.
