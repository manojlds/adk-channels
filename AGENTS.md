---
name: adk-channels
description: Multi-channel messaging integration for Google ADK
---

## Overview

`adk-channels` is a Python package that lets Google ADK agents receive and send messages through external messaging platforms (Slack, Telegram, Webhooks). It's inspired by [pi-channels](https://github.com/espennilsen/pi/tree/main/extensions/pi-channels) for the Pi coding agent.

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

## Directory Layout

```
src/adk_channels/
├── __init__.py
├── types.py          # Shared types (ChannelMessage, IncomingMessage, etc.)
├── config.py         # Configuration loading from env / JSON / pydantic-settings
├── registry.py       # Adapter registry and route resolution
├── bridge.py         # Chat bridge: routes incoming messages to ADK agent
├── server.py         # Optional FastAPI server for webhook/HTTP adapters
└── adapters/
    ├── __init__.py
    ├── base.py       # Base adapter interface
    ├── slack.py      # Slack adapter (Bolt + Socket Mode)
    ├── telegram.py   # Telegram adapter (python-telegram-bot)
    └── webhook.py    # Webhook adapter (outgoing HTTP POST)
```

## Conventions

- **Python 3.10+** with type hints
- **Pydantic v2** for configuration and data validation
- **Async-first** where possible; adapters expose async APIs
- **Minimal dependencies**: core package only requires `google-adk` and `pydantic`
- **Optional extras**: `slack`, `telegram`, `webhook`, or `all`

## Adapter Interface

All adapters implement `BaseChannelAdapter`:

```python
class BaseChannelAdapter(ABC):
    direction: AdapterDirection  # "incoming" | "outgoing" | "bidirectional"

    async def send(self, message: ChannelMessage) -> None: ...
    async def start(self, on_message: OnIncomingMessage) -> None: ...
    async def stop(self) -> None: ...
    async def send_typing(self, recipient: str) -> None: ...
```

## Configuration

Loaded via `pydantic-settings` (env vars override JSON files):

```python
class ChannelsConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADK_CHANNELS_", env_nested_delimiter="__")

    adapters: dict[str, AdapterConfig]
    routes: dict[str, RouteConfig] = {}
    bridge: BridgeConfig = BridgeConfig()
```

Env var examples:
- `ADK_CHANNELS_ADAPTERS__SLACK__TYPE=slack`
- `ADK_CHANNELS_ADAPTERS__SLACK__BOT_TOKEN=xoxb-...`
- `ADK_CHANNELS_ADAPTERS__SLACK__APP_TOKEN=xapp-...`

## Bridge

The `ChatBridge` connects incoming messages to an ADK agent:

1. Receives `IncomingMessage` from any adapter
2. Optionally maintains per-sender conversation sessions (persistent vs stateless)
3. Invokes the ADK agent with the message content
4. Sends the agent's response back via the originating adapter

Session modes:
- `persistent` — conversation memory maintained per sender
- `stateless` — isolated invocation per message (no memory)

## Tooling

This project uses **uv** for dependency management and virtual environments, and **ruff** for linting and formatting.

### Setup

```bash
# Create virtual environment
uv venv

# Install with all extras + dev tools
uv sync --all-extras --group dev
```

### Testing

```bash
uv run pytest tests/
```

### Lint / Format

```bash
uv run ruff check src/
uv run ruff format src/
```

### Typecheck

```bash
uv run mypy src/
```
