---
name: adk-channels
description: Multi-channel messaging integration for Google ADK — Slack, Telegram, Webhooks
---

## Overview

`adk-channels` is a Python library that connects Google ADK agents to external messaging platforms.
Incoming messages are routed through a chat bridge to ADK agents; responses are sent back
automatically. Supports both single-agent and multi-app FastAPI deployments through one bridge.

## Directory Layout

```
src/adk_channels/
├── types.py              # Shared dataclasses
├── config.py             # Pydantic-settings config (env vars + JSON)
├── registry.py           # Adapter registry and route resolution
├── bridge.py             # Unified bridge (single-agent + multi-app routing)
├── server_integration.py # FastAPI integration utilities
├── server.py             # Standalone webhook server (optional)
└── adapters/
    ├── base.py           # BaseChannelAdapter interface
    ├── slack.py          # Bolt + Socket Mode adapter
    ├── telegram.py       # python-telegram-bot adapter
    └── webhook.py        # Outgoing HTTP POST adapter

tests/                    # pytest suite
examples/                 # Usage examples
```

## Tooling

Use **uv** and **ruff** exclusively.

```bash
# Setup
uv venv
uv sync --all-extras --group dev

# Quality gate (run after every change)
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```

Do not consider a change complete until all four commands pass.

## Conventions

- **Python 3.10+** with type hints (`from __future__ import annotations`)
- **Pydantic v2** for config and data validation
- **Async-first**; adapters expose async APIs
- **Line length**: 120 (configured in `pyproject.toml`)
- **Import order**: stdlib → third-party → local (enforced by ruff)
- **Lazy imports**: Optional adapter dependencies are imported inside methods to avoid hard deps
- **Minimal logging**: Use module-level `logging.getLogger(...)`, not `print`

## Adapter Interface

All adapters implement `BaseChannelAdapter`:

```python
class BaseChannelAdapter(ABC):
    direction: AdapterDirection  # "incoming" | "outgoing" | "bidirectional"

    async def send(self, message: ChannelMessage) -> None: ...
    async def start(self, on_message: OnIncomingMessage) -> None: ...
    async def stop(self) -> None: ...
    async def send_typing(self, recipient: str) -> None: ...  # optional, no-op default
```

Register built-in adapters via factory functions in `registry.py`.
Custom adapters can be registered at runtime with `registry.register(name, adapter)`.

## Bridge Patterns

### Unified (`ChatBridge`)
Use one bridge for both patterns:

```python
# Single-agent
bridge = ChatBridge(config.bridge, registry, agent_factory=lambda: my_agent)

# Multi-app routing
bridge = ChatBridge(
    config.bridge,
    registry,
    app_resolver=lambda msg: "support" if "support" in msg.sender else "default",
    agent_factories={"support": create_support_agent, "default": create_default_agent},
)
```

Dispatch priority:
1. `agent_runners` — custom async/sync callable
2. `http_clients` — HTTP client calling ADK endpoints
3. `agent_factories` — direct ADK Runner invocation

## Configuration

Loaded via `pydantic-settings` with `env_prefix="ADK_CHANNELS_"` and nested delimiter `__`.

```bash
ADK_CHANNELS_ADAPTERS__SLACK__TYPE=slack
ADK_CHANNELS_ADAPTERS__SLACK__BOT_TOKEN=xoxb-...
ADK_CHANNELS_BRIDGE__MAX_CONCURRENT=2
```

Or load from JSON:

```python
config = ChannelsConfig.from_file("channels.json")
```

## Testing

- Test runner: `pytest` with `pytest-asyncio` (`asyncio_mode = "auto"`)
- Fake adapters: implement the base interface for unit tests
- Bridge tests: verify queue depth, concurrency limits, and routing

## Review Checklist

- [ ] All adapters handle `stop()` correctly (no dangling connections)
- [ ] Bridge respects `max_queue_per_sender` and `max_concurrent`
- [ ] Secrets are not logged or exposed in exceptions
- [ ] Lazy imports work when optional extras are not installed
- [ ] Type annotations cover public API surface
- [ ] New features include tests and README updates
