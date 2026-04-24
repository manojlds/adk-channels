# adk-channels

Multi-channel messaging integration for [Google ADK](https://github.com/google/adk) — interact with your agents via **Slack**, **Telegram**, and **webhooks**.

Inspired by [pi-channels](https://github.com/espennilsen/pi/tree/main/extensions/pi-channels) for the Pi coding agent.

## Features

- **Slack adapter** — bidirectional via Bolt + Socket Mode; supports @mentions, slash commands, threads, and channel allowlisting
- **Telegram adapter** — bidirectional via Bot API with polling
- **Webhook adapter** — outgoing HTTP POST to any URL with customizable headers and payload modes
- **Chat bridge** — incoming messages are routed to your ADK agent; responses sent back automatically
- **Per-sender sessions** — persistent conversation memory per user, or stateless mode per message
- **Queue management** — FIFO queue per sender with configurable depth and concurrency limits
- **Route aliases** — friendly names for adapter+recipient combos (e.g. `ops` → `slack:#alerts`)

## Quick Start

### Installation

```bash
# Core only (no adapters)
uv pip install adk-channels

# With Slack support
uv pip install adk-channels[slack]

# With all adapters
uv pip install adk-channels[all]
```

### Slack Setup

1. Create a Slack app at https://api.slack.com/apps
2. Enable **Socket Mode** and generate an **App-Level Token** (`xapp-...`)
3. Go to **OAuth & Permissions**, install the app to your workspace, and copy the **Bot Token** (`xoxb-...`)
4. Add the following bot token scopes:
   - `chat:write`
   - `app_mentions:read`
   - `commands` (if using slash commands)
   - `im:history`, `channels:history`, `groups:history` (as needed)

### Environment Variables

```bash
export ADK_CHANNELS_ADAPTERS__SLACK__TYPE=slack
export ADK_CHANNELS_ADAPTERS__SLACK__BOT_TOKEN=xoxb-your-bot-token
export ADK_CHANNELS_ADAPTERS__SLACK__APP_TOKEN=xapp-your-app-token
export ADK_CHANNELS_BRIDGE__ENABLED=true

# Optional: restrict to specific channels
export ADK_CHANNELS_ADAPTERS__SLACK__ALLOWED_CHANNEL_IDS='["C0123456789"]'

# Optional: only respond to @mentions in channels
export ADK_CHANNELS_ADAPTERS__SLACK__RESPOND_TO_MENTIONS_ONLY=true
```

### Example: Slack Agent

```python
import asyncio
import os
from adk_channels import ChannelRegistry, ChatBridge, ChannelsConfig
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService


def create_agent() -> Agent:
    return Agent(
        model="gemini-2.0-flash",
        name="slack_assistant",
        instruction="You are a helpful AI assistant in Slack. Keep responses concise and use markdown.",
    )


async def main():
    config = ChannelsConfig()
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

    print("Agent is running on Slack. Press Ctrl+C to stop.")
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
```

Run it:
```bash
uv run python your_agent.py
```

Then in Slack:
- DM the bot directly
- @mention the bot in a channel: `@YourBot what is the weather?`
- Use a slash command: `/adk write a python function to reverse a string`

## FastAPI + Multi-App Deployment

In production ADK deployments, you typically run multiple agent "apps" as FastAPI endpoints. `adk-channels` integrates cleanly with this pattern:

```
FastAPI App
├── /agents/support/run          (ADK support agent endpoint)
├── /agents/engineering/run      (ADK engineering agent endpoint)
├── /channels/webhook/{adapter}  (Channel webhook receivers)
├── /channels/health             (Channels healthcheck)
└── Background: Slack Socket Mode, Telegram polling
```

### Example: Multi-App Server

```python
from fastapi import FastAPI
from google.adk.agents import Agent
from google.adk.sessions import InMemorySessionService
from adk_channels import ChannelsConfig, ChannelRegistry
from adk_channels.multi_app_bridge import MultiAppBridge
from adk_channels.server_integration import ChannelsFastAPIIntegration
import uvicorn

# Define multiple agents
support_agent = Agent(
    model="gemini-2.0-flash",
    name="support_bot",
    instruction="You are a customer support assistant...",
)

eng_agent = Agent(
    model="gemini-2.0-flash",
    name="engineering_bot",
    instruction="You are an engineering assistant...",
)

# Route messages to the right agent based on Slack channel
CHANNEL_MAP = {
    "C0SUPPORT123": "support",
    "C0ENG123456": "engineering",
}

def app_resolver(message):
    channel_id = message.sender.split(":")[0]
    return CHANNEL_MAP.get(channel_id, "default")

# Create FastAPI app
app = FastAPI(title="ADK Multi-App Server")

# Configure channels
config = ChannelsConfig()
registry = ChannelRegistry()

bridge = MultiAppBridge(
    bridge_config=config.bridge,
    registry=registry,
    app_resolver=app_resolver,
    agent_factories={
        "support": lambda: support_agent,
        "engineering": lambda: eng_agent,
        "default": lambda: support_agent,
    },
    session_service_factory=InMemorySessionService,
)

# Integrate channels into FastAPI
integration = ChannelsFastAPIIntegration(
    fastapi_app=app,
    registry=registry,
    bridge=bridge,
)
integration.setup()

# Run with uvicorn
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### Routing Strategies

The `app_resolver` function is called for every incoming message. You can route based on:

- **Slack channel**: `message.metadata.get("channel_id")`
- **Telegram chat**: `message.sender`
- **Message content**: `"support" in message.text`
- **User ID**: `message.metadata.get("user_id")`
- **Adapter type**: `message.adapter == "slack"`

### Dispatch Patterns

`MultiAppBridge` supports three ways to invoke agents (checked in order):

1. **`agent_runners`** — Custom async/sync callables: `runner(app_name, session_id, text) -> str`
2. **`http_clients`** — HTTP clients that call ADK endpoints internally: `client(session_id, text) -> str`
3. **`agent_factories`** — Direct ADK `Runner` invocation with optional shared `SessionService`

### Convenience Factory

For simple setups, use the factory:

```python
from adk_channels.server_integration import create_fastapi_app

app = create_fastapi_app(
    agents={
        "support": support_agent,
        "engineering": eng_agent,
    },
    app_resolver=lambda msg: "support" if "help" in msg.text else "engineering",
)
```

## Configuration

Configuration is loaded via `pydantic-settings` with env var support.

### Env Var Format

Use double underscores (`__`) as nested delimiters:

```bash
# Adapter config
ADK_CHANNELS_ADAPTERS__SLACK__TYPE=slack
ADK_CHANNELS_ADAPTERS__SLACK__BOT_TOKEN=xoxb-...
ADK_CHANNELS_ADAPTERS__SLACK__APP_TOKEN=xapp-...

# Routes
ADK_CHANNELS_ROUTES__OPS__ADAPTER=slack
ADK_CHANNELS_ROUTES__OPS__RECIPIENT=C0123456789

# Bridge
ADK_CHANNELS_BRIDGE__ENABLED=true
ADK_CHANNELS_BRIDGE__SESSION_MODE=persistent
ADK_CHANNELS_BRIDGE__MAX_QUEUE_PER_SENDER=5
ADK_CHANNELS_BRIDGE__MAX_CONCURRENT=2
```

### JSON Config File

```python
from adk_channels import ChannelsConfig

config = ChannelsConfig.from_file("channels.json")
```

Example `channels.json`:
```json
{
  "adapters": {
    "slack": {
      "type": "slack",
      "bot_token": "xoxb-...",
      "app_token": "xapp-...",
      "allowed_channel_ids": ["C0123456789"],
      "respond_to_mentions_only": true,
      "slash_command": "/adk"
    },
    "telegram": {
      "type": "telegram",
      "bot_token": "your-telegram-bot-token"
    },
    "alerts": {
      "type": "webhook",
      "url": "https://hooks.example.com/alerts"
    }
  },
  "routes": {
    "ops": {
      "adapter": "slack",
      "recipient": "C0123456789"
    }
  },
  "bridge": {
    "enabled": true,
    "session_mode": "persistent",
    "max_concurrent": 2
  }
}
```

## Adapters

### Slack

| Config Key | Type | Description |
|------------|------|-------------|
| `type` | `string` | Must be `"slack"` |
| `bot_token` | `string` | Bot User OAuth Token (`xoxb-...`) |
| `app_token` | `string` | App-Level Token for Socket Mode (`xapp-...`) |
| `allowed_channel_ids` | `string[]` | Optional allowlist of channel IDs |
| `respond_to_mentions_only` | `boolean` | Only respond to @mentions in channels |
| `slash_command` | `string` | Slash command to register (default: `/adk`) |

**Features:**
- Responds to DMs automatically
- Responds to @mentions in channels (if `respond_to_mentions_only` is false, responds to all messages in allowed channels)
- Supports slash commands with instant acknowledgment
- Thread-aware: replies in the same thread
- Long message splitting (splits at 3000 chars)

### Telegram

| Config Key | Type | Description |
|------------|------|-------------|
| `type` | `string` | Must be `"telegram"` |
| `bot_token` | `string` | Telegram Bot API token |
| `allowed_chat_ids` | `int[]` | Optional allowlist of chat IDs |
| `parse_mode` | `string` | Message parse mode: `Markdown`, `HTML`, etc. |

**Features:**
- Responds to text messages
- Typing indicators while processing
- Long message splitting (splits at 4096 chars)
- `/start` command support

### Webhook

| Config Key | Type | Description |
|------------|------|-------------|
| `type` | `string` | Must be `"webhook"` |
| `url` | `string` | Default webhook URL |
| `method` | `string` | HTTP method (default: `POST`) |
| `headers` | `object` | Default headers to include |
| `content_type` | `string` | Default Content-Type |
| `payload_mode` | `string` | `"envelope"` (default) or `"raw"` |

**Envelope mode** sends a JSON payload:
```json
{
  "text": "message text",
  "source": "adk",
  "adapter": "webhook",
  "recipient": "https://hooks.example.com/alerts",
  "metadata": {},
  "timestamp": 1234567890
}
```

**Raw mode** sends the text directly (or a custom body via metadata).

## Chat Bridge

The bridge connects incoming messages to your ADK agent:

1. Message arrives from Slack/Telegram
2. Bridge enqueues it (per-sender FIFO queue)
3. ADK agent is invoked with the message text
4. Agent response is sent back via the same adapter

### Session Modes

- **`persistent`** (default): Uses the same ADK session per sender, maintaining conversation history
- **`stateless`**: Creates a new isolated session per message (no memory)

### Bridge Config

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable the bridge |
| `session_mode` | `"persistent"` | `"persistent"` or `"stateless"` |
| `session_rules` | `[]` | Per-sender mode overrides (glob patterns) |
| `idle_timeout_minutes` | `30` | Kill idle persistent sessions after N min |
| `max_queue_per_sender` | `5` | Max queued messages per sender |
| `timeout_ms` | `300000` | Per-prompt timeout (5 min) |
| `max_concurrent` | `2` | Max senders processed in parallel |
| `typing_indicators` | `true` | Send typing indicators while processing |

### Custom Agent Runner

Instead of using `agent_factory`, you can provide a custom `agent_runner`:

```python
async def my_runner(session_id: str, text: str) -> str:
    # Your custom logic here
    return f"Echo: {text}"

bridge = ChatBridge(
    bridge_config=config.bridge,
    registry=registry,
    agent_runner=my_runner,
)
```

## Programmatic API

### Send a message

```python
result = await registry.send(
    ChannelMessage(
        adapter="slack",
        recipient="C0123456789",
        text="Hello from the agent!",
    )
)
# result -> {"ok": True} or {"ok": False, "error": "..."}
```

### Use route aliases

```python
# With route "ops" -> {adapter: "slack", recipient: "C0123456789"}
result = await registry.send(
    ChannelMessage(adapter="ops", recipient="", text="Alert!")
)
```

### Register a custom adapter

```python
from adk_channels.adapters.base import BaseChannelAdapter

class MyAdapter(BaseChannelAdapter):
    direction = AdapterDirection.BIDIRECTIONAL
    async def send(self, message): ...
    async def start(self, on_message): ...
    async def stop(self): ...

registry.register("custom", MyAdapter())
```

## Development

```bash
# Setup
uv venv
uv sync --all-extras --group dev

# Lint
uv run ruff check src/
uv run ruff format src/

# Typecheck
uv run mypy src/

# Test
uv run pytest tests/
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      ADK Channels                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Slack     │  │  Telegram   │  │      Webhook        │  │
│  │  Adapter    │  │  Adapter    │  │     Adapter         │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
│         └─────────────────┴────────────────────┘             │
│                           │                                  │
│                    ┌──────┴──────┐                           │
│                    │   Registry  │                            │
│                    └──────┬──────┘                           │
│                           │                                  │
│                    ┌──────┴──────┐                           │
│                    │   Bridge    │◄── routes incoming msgs    │
│                    │             │    to ADK agent            │
│                    └──────┬──────┘                           │
│                           │                                  │
│                    ┌──────┴──────┐                           │
│                    │  ADK Agent  │                            │
│                    │  (Runner)   │                            │
│                    └─────────────┘                            │
└─────────────────────────────────────────────────────────────┘
```

## License

MIT
