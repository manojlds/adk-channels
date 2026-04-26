# adk-channels

Multi-channel messaging integration for [Google ADK](https://github.com/google/adk) — interact with your agents via **Slack**, **Telegram**, and **webhooks**.

Inspired by [pi-channels](https://github.com/espennilsen/pi/tree/main/extensions/pi-channels) for the Pi coding agent.

## Features

- **Slack adapter** — bidirectional via Bolt + Socket Mode; supports @mentions, slash commands, threads, interactive actions, and channel allowlisting
- **Telegram adapter** — bidirectional via Bot API with polling
- **Webhook adapter** — outgoing HTTP POST to any URL with customizable headers and payload modes
- **Chat bridge** — incoming messages are routed to your ADK agent; responses sent back automatically
- **Configurable session keys** — persist by sender/user/channel/thread with optional per-pattern rules
- **Queue management** — FIFO queue per sender with configurable depth and concurrency limits
- **Tool interaction runtime** — `ToolActionRouter` handles Slack action callbacks before agent execution
- **Generative UI helpers** — build approval/select/info tool payloads with `tool_approval`, `tool_single_select`, `tool_multi_select`, `tool_info`
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

### Setup Levels

Pick one path based on how much capability you need right now:

| Level | Best For | Bridge | Example |
|------|----------|--------|---------|
| Basic | One agent, plain chat, fastest setup | `ChatBridge` | `examples/basic_slack_agent.py` |
| Intermediate | Tool-driven Slack interactions (approval/select/info) | `ChatBridge` + `ToolActionRouter` | `examples/slack_fastapi.py` |
| Advanced | Multi-agent routing, FastAPI deployment, per-app behavior | `ChatBridge` + `app_resolver` + `ToolActionRouter` | `examples/multi_app_server/main.py` |

Need full copy/paste files? See `TEMPLATES.md` for complete Basic/Intermediate/Advanced templates.

### Slack Setup

1. Create a Slack app at https://api.slack.com/apps
2. Enable **Socket Mode** and generate an **App-Level Token** (`xapp-...`)
3. Go to **OAuth & Permissions**, install the app to your workspace, and copy the **Bot Token** (`xoxb-...`)
4. Add the following bot token scopes:
   - `chat:write`
   - `app_mentions:read`
   - `commands` (if using slash commands)
   - `im:history`, `channels:history`, `groups:history` (as needed)
5. Enable **Event Subscriptions** and subscribe to `app_mention` and `message.im`; add `message.channels` and `message.groups` if you want replies in bot-started channel threads to continue without re-mentioning the bot
6. Enable **Interactivity & Shortcuts** if you use block buttons/selects in messages

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

# Optional: disable automatic thread replies for top-level channel @mentions
export ADK_CHANNELS_ADAPTERS__SLACK__REPLY_IN_THREAD_BY_DEFAULT=false

# Optional: require @mentions even inside bot-started channel threads
export ADK_CHANNELS_ADAPTERS__SLACK__CONTINUE_THREADS_WITHOUT_MENTION=false

# Optional: durable SQLite ADK sessions for examples
export ADK_CHANNELS_SESSION_DB=.adk_channels/sessions.sqlite
```

### Basic Setup (Single Agent)

Step-by-step:

1. Configure Slack env vars (shown above).
2. Run the minimal single-agent example:

```bash
uv run python examples/basic_slack_agent.py
```

3. In Slack, DM or @mention the bot.

What you get:
- One ADK agent behind `ChatBridge`
- Session memory + queue management
- No explicit interaction routing required

### Intermediate Setup (Tool-Driven Slack UI)

Step-by-step:

1. Keep the same Slack env vars and ensure Interactivity is enabled in your Slack app.
2. Run the interactive tool example:

```bash
uv run python examples/slack_fastapi.py
```

3. In Slack, ask for tool workflows (examples):
   - `list internal files`
   - `delete deployment-plan.md`
   - `choose multiple files for cleanup`

What you get:
- Structured tool payload rendering (`tool_approval`, `tool_multi_select`, etc.)
- Action callback handling through `ToolActionRouter`
- Native Slack buttons/selects with ADK tool semantics

### Advanced Setup (Multi-App FastAPI Deployment)

Step-by-step:

1. Define per-channel or per-message routing logic in `app_resolver`.
2. Configure multiple agent backends (`support`, `engineering`, `default`, etc.).
3. Run multi-app example:

```bash
uv run python examples/multi_app_server/main.py
```

What you get:
- One Slack app, multiple ADK agent apps
- Per-app sessions and routing
- Optional interaction handling before app dispatch via `interaction_handler`

## FastAPI + Multi-App Deployment

In production ADK deployments, you typically run multiple agent "apps" as FastAPI endpoints. `adk-channels` integrates cleanly with this pattern:

```
FastAPI App
├── /agents/support/run          (ADK support agent endpoint)
├── /agents/engineering/run      (ADK engineering agent endpoint)
├── /channels/health             (Channels healthcheck)
├── /channels/status             (Channels status)
└── Background: Slack Socket Mode, Telegram polling
```

### Example: Multi-App Server

```python
from pathlib import Path

from fastapi import FastAPI
from google.adk.agents import Agent
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from adk_channels import ChannelsConfig, ChannelRegistry, ChatBridge
from adk_channels.server_integration import ChannelsFastAPIIntegration
import uvicorn

SESSION_DB = Path(".adk_channels/sessions.sqlite")

def create_session_service():
    SESSION_DB.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSessionService(str(SESSION_DB))

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

bridge = ChatBridge(
    bridge_config=config.bridge,
    registry=registry,
    app_resolver=app_resolver,
    agent_factories={
        "support": lambda: support_agent,
        "engineering": lambda: eng_agent,
        "default": lambda: support_agent,
    },
    session_service_factory=create_session_service,
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

`ChatBridge` with `app_resolver` supports three ways to invoke agents (checked in order):

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
    interaction_handler=router,
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
    "session_scope": "sender",
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
| `reply_in_thread_by_default` | `boolean` | For top-level channel @mentions, reply in a new thread and use that thread as sender/session key (default: `true`) |
| `continue_threads_without_mention` | `boolean` | Continue bot-started channel threads when users reply without @mentioning the bot (default: `true`) |
| `slash_command` | `string` | Slash command to register (default: `/adk`) |
| `processing_reaction` | `string` | Optional reaction name to add when a Slack message is accepted for processing; requires `reactions:write` |
| `completed_reaction` | `string` | Optional reaction name to add after a reply is sent; requires `reactions:write` |

**Startup checks:**
- The Slack adapter calls `auth.test` at startup and reads the `x-oauth-scopes` response header.
- Startup fails if the bot token cannot be authenticated or is missing the minimum bot scopes: `chat:write`, `app_mentions:read`.
- Optional capabilities are detected from granted scopes and exposed in adapter status: DMs (`im:history`), public channel message events (`channels:history`), private channel message events (`groups:history`), MPIM events (`mpim:history`), slash commands (`commands`), reactions (`reactions:write`), file downloads (`files:read`), file uploads (`files:write`), and user lookup (`users:read`).
- Socket Mode still requires an App-Level Token (`xapp-...`) with `connections:write`; Slack validates that when the Socket Mode connection is opened.
- Scope checks do not prove Event Subscriptions or channel membership are configured; Slack only delivers events the app has subscribed to and conversations the bot can access.

**Features:**
- Responds to DMs automatically when `im:history` is granted and `message.im` is subscribed
- Responds to @mentions in channels (if `respond_to_mentions_only` is false, responds to all messages in allowed channels)
- Supports slash commands with instant acknowledgment
- Thread-aware: top-level channel @mentions start a thread by default; replies in bot-started threads continue without repeated @mentions when channel message events are subscribed; top-level DMs stay in the DM conversation
- Translates interactive block actions (buttons/selects) into bridge `IncomingMessage` events
- Long message splitting (splits at 3000 chars)
- Tool interaction translation (ADK tool-call/tool-result events rendered as Slack-native blocks)
- Optional processing/completed reactions when `reactions:write` is available

**Interactive tool prompts:**

```python
from adk_channels import ChannelMessage, build_tool_actions_blocks, build_tool_button

blocks = build_tool_actions_blocks(
    prompt_text="Choose the next step:",
    buttons=[
        build_tool_button(label="Run tests", tool_name="ci", action="run_tests", value={"suite": "quick"}),
        build_tool_button(label="Cancel", tool_name="ci", action="cancel", style="danger"),
    ],
)

await registry.send(
    ChannelMessage(
        adapter="slack",
        recipient="C0123456789",
        text="Interactive tool prompt",
        metadata={"slack_blocks": blocks},
    )
)
```

When a user clicks a button/select, the Slack adapter emits an `IncomingMessage` with:
- `text`: `action:<action_id> value:<value>`
- `metadata.event_type`: `block_action`
- `metadata.tool_name` and `metadata.tool_action` when `action_id` follows `adk.tool.<tool>.<action>`

### Tool UI Runtime (Slack Generative UI)

For ADK tool-driven apps, use this three-part pattern:

1. Tool returns structured payload (message + interactive controls)
2. Slack adapter renders it into native blocks/actions
3. `ToolActionRouter` handles action callbacks and optionally short-circuits the bridge

#### Tool payload helpers

```python
from adk_channels import tool_approval, tool_info, tool_multi_select

def request_delete(file_name: str) -> dict:
    request_id = "req-123"
    return tool_approval(
        message=f"Approval requested to delete `{file_name}`.",
        tool_name="approval",
        value={"request_id": request_id},
        actions_text=f"Approve deleting `{file_name}`?",
        block_id=f"approval_{request_id}",
    )

def pick_targets() -> dict:
    return tool_multi_select(
        message="Waiting for selection.",
        tool_name="options",
        action="choose",
        options=["backend", "frontend", "qa"],
        placeholder="Select targets",
    )

def list_items() -> dict:
    return tool_info("Available items: backend, frontend, qa")
```

#### Action routing

```python
from adk_channels import ToolActionRouter

router = ToolActionRouter()

@router.on_tool("approval", "approve")
@router.on_tool("approval", "reject")
def handle_approval(ctx):
    payload = ctx.action_value_json()
    request_id = payload.get("request_id")
    decision = ctx.tool_action
    return f"Recorded decision {decision} for request {request_id}"

@router.on_tool("options", "choose")
def handle_options(ctx):
    selected = ctx.action_values()
    return f"Selected options: {', '.join(selected) if selected else 'none'}"
```

Pass the router into either bridge:

```python
bridge = ChatBridge(..., interaction_handler=router)
```

If a handler returns:
- `str` -> bridge sends a reply to the same adapter+sender
- `ChannelMessage` or `list[ChannelMessage]` -> bridge sends those messages
- `True` -> handled, no reply
- `False`/`None` -> unhandled, message continues to normal agent routing

#### Tool UI patterns

| Pattern | Helper | Typical Use |
|---------|--------|-------------|
| Info | `tool_info` | Simple status/result cards |
| Approval | `tool_approval` | Destructive operations, human-in-the-loop controls |
| Single select | `tool_single_select` | Pick one environment/owner/version |
| Multi select | `tool_multi_select` | Pick multiple files/services/targets |

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

- **`persistent`** (default): Reuses the same ADK session for the resolved session key
- **`stateless`**: Creates a new isolated ADK session for each message

### Session Scope

Choose how the bridge derives the session key:

- **`sender`** (default): Adapter sender ID (`slack:C123` or `slack:C123:thread_ts`)
- **`user`**: Prefer `metadata.user_id` (falls back to sender)
- **`channel`**: Channel/chat level memory
- **`thread`**: Thread-level memory when thread metadata exists

Use `session_rules` with glob patterns to override mode for subsets of traffic (for example, keep DMs persistent while making shared channels stateless).

### Durable ADK Sessions

The bridge always derives the same run session id for the same persistent sender key. For Slack thread conversations, that sender key is `channel_id:thread_ts`, so a durable ADK session service can restore the agent context even after the bridge prunes its in-memory queue state or the process restarts.

The examples use ADK's `SqliteSessionService` by default. Set `ADK_CHANNELS_SESSION_DB` to choose the database path; otherwise examples use `.adk_channels/sessions.sqlite`.

For Slack thread replies where the bot is not `@mentioned`, the adapter marks the message as requiring an existing session. The bridge then checks the configured shared session service for the same `channel_id:thread_ts` run session id before enqueueing it. If no session exists, the message is ignored instead of starting a new channel thread conversation accidentally.

This durable continuation check works with direct ADK Runner dispatch (`agent_factories`) and with `agent_runner` or `http_clients` when you provide `session_service_factory` pointing at the same durable ADK session store used by that runner/client. Without a shared session service, thread replies where the bot is not `@mentioned` can continue only while the bridge still has an active in-memory session; after a restart or prune, mention the bot once in the thread to resume or create the durable session.

Ephemeral state still exists outside the ADK session service:
- In-flight bridge queues and concurrency slots
- Short-lived Slack duplicate event suppression
- Example-only in-memory tool state such as pending approval dictionaries

### Bridge Config

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable the bridge |
| `session_mode` | `"persistent"` | `"persistent"` or `"stateless"` |
| `session_scope` | `"sender"` | `"sender"`, `"user"`, `"channel"`, or `"thread"` |
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

`ChatBridge` accepts `interaction_handler` for action callbacks:

```python
bridge = ChatBridge(
    bridge_config=config.bridge,
    registry=registry,
    agent_factory=create_agent,
    interaction_handler=router,  # ToolActionRouter or custom callable
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

## ADK Implementation Guide (End-to-End)

Use this blueprint when integrating channels into a real ADK app.

### Basic Track (Single Agent Chat)

1. Build one ADK agent.
2. Configure one adapter (usually Slack first).
3. Use `ChatBridge` without `interaction_handler`.
4. Start listening via `registry.set_on_incoming(bridge.handle_message)` and `registry.start_listening()`.

Use this when you just need chat + memory + queueing.

### Intermediate Track (Tool-Driven Generative UI)

1. Keep `ChatBridge` and add tools that return structured payloads (`tool_approval`, `tool_single_select`, `tool_multi_select`, `tool_info`).
2. Create a `ToolActionRouter` and register handlers for tool actions.
3. Pass router as `interaction_handler` to `ChatBridge`.
4. Keep tool outputs deterministic and include IDs (`request_id`, task IDs) for idempotency.

Use this when users need approvals, selections, and interactive Slack controls.

### Advanced Track (Multi-App + FastAPI)

1. Split responsibilities into multiple agents/apps (`support`, `engineering`, `ops`, etc.).
2. Route with `app_resolver` in `ChatBridge`.
3. Add `interaction_handler` to run interactive callbacks before app routing.
4. Use shared/persistent session services where needed.
5. Expose health/status endpoints through `ChannelsFastAPIIntegration`.

Use this for production setups with multiple agent domains and channel routing rules.

### Production Checklist

1. **Session strategy**
   - Set `session_mode` (`persistent` or `stateless`)
   - Set `session_scope` (`sender`, `user`, `channel`, `thread`)
   - Add `session_rules` for mixed policies

2. **Reliability**
   - Configure `max_queue_per_sender`, `max_concurrent`, `timeout_ms`
   - Use route aliases for stable destinations
   - Monitor `/channels/health` and `/channels/status`

3. **Security and governance**
   - Restrict Slack channels with `allowed_channel_ids`
   - Avoid exposing raw secrets in tool payloads
   - Log request/action IDs for audits

4. **Cross-channel design**
   - Keep tool logic channel-agnostic
   - Provide fallback text for non-interactive channels
   - Treat Slack UI hints as optional rendering metadata

### Example architecture for tool-heavy ADK bots

```text
User -> Slack message/action
     -> Slack adapter
     -> (optional) interaction_handler (ToolActionRouter)
         -> side-effect + confirmation reply
         -> OR pass-through to bridge
     -> ChatBridge
     -> ADK runner
     -> Tool call / tool result
     -> Slack adapter renders UI blocks
```

This gives you a generative UI flow similar to web chat apps, but native to Slack.

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
