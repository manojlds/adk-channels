# DRS Project Context

## What adk-channels Is
A Python library that lets Google ADK agents receive and send messages through external
messaging platforms (Slack, Telegram, Webhooks). It provides a chat bridge that routes
incoming messages to ADK agents and sends responses back automatically, with support for
multi-app deployments via FastAPI.

## Architecture
- `src/adk_channels/types.py`: Shared dataclasses (ChannelMessage, IncomingMessage, RunResult, etc.)
- `src/adk_channels/config.py`: Pydantic-settings configuration with env var support
- `src/adk_channels/registry.py`: Adapter registry and route resolution
- `src/adk_channels/adapters/`: Built-in adapters — Slack (Bolt + Socket Mode), Telegram (python-telegram-bot), Webhook (HTTP POST)
- `src/adk_channels/bridge.py`: Single-app chat bridge connecting channels to one ADK agent
- `src/adk_channels/multi_app_bridge.py`: Multi-app bridge routing messages to different agents/apps
- `src/adk_channels/server_integration.py`: FastAPI integration for multi-app deployments
- `tests/`: Unit and integration tests for core, adapters, bridges, and server integration

## Technology Stack
- **Language**: Python 3.10+
- **Agent framework**: Google ADK
- **Config**: Pydantic v2, pydantic-settings
- **Packaging/build**: Hatchling, `uv`
- **Testing**: `pytest`, `pytest-asyncio`
- **Lint/format**: `ruff`
- **Type checking**: `mypy`

## Trust Boundaries

### Trusted Inputs
- **Environment variables**: Used for adapter tokens, app tokens, and pydantic-settings config
- **Local config files**: JSON config files loaded via `ChannelsConfig.from_file()`
- **ADK agent code**: The library is embedded in trusted Python applications

### External Inputs (Untrusted)
- **Slack events**: Messages, @mentions, slash commands from Slack workspace users
- **Telegram messages**: Text messages from Telegram chat users
- **Webhook payloads**: HTTP POST bodies from external systems

### What Actually Matters
- Adapter tokens/secrets must not leak in logs or exceptions
- Slack channel allowlisting must be enforced
- Webhook payloads should be validated before processing
- Do not execute arbitrary code from message content

## Review Focus
- Correctness of adapter event handling (Slack Socket Mode, Telegram polling)
- Bridge queue and concurrency management (per-sender FIFO, max_concurrent)
- Multi-app routing logic and session isolation
- Error handling in async adapter lifecycle (start/stop)
- Type safety across adapter interfaces and bridge dispatch
- Secrets handling in config and adapter initialization

## Avoid Over-Flagging
- Standard Python async patterns are not concurrency bugs
- Adapter imports inside methods are lazy-loading for optional deps, not issues
- `try/except/pass` for typing indicators is intentional graceful degradation
