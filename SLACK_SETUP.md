# Slack Setup Guide for adk-channels

This guide walks you through creating a Slack app, getting tokens, and connecting it to your ADK agent.

## Table of Contents

1. [Create a Slack App](#1-create-a-slack-app)
2. [Enable Socket Mode](#2-enable-socket-mode)
3. [Get Your Tokens](#3-get-your-tokens)
4. [Set Bot Permissions](#4-set-bot-permissions)
5. [Install the App to Your Workspace](#5-install-the-app-to-your-workspace)
6. [Invite the Bot to Channels](#6-invite-the-bot-to-channels)
7. [Run Your ADK Agent](#7-run-your-adk-agent)
8. [Test in Slack](#8-test-in-slack)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Create a Slack App

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App**
3. Choose **From scratch**
4. Enter an app name (e.g., "ADK Assistant") and select your workspace
5. Click **Create App**

## 2. Enable Socket Mode

Socket Mode lets your bot receive events via WebSocket without needing a public URL.

1. In the left sidebar, click **Socket Mode**
2. Toggle **Enable Socket Mode** to ON
3. You'll be prompted to generate an app-level token — do this and note it down (starts with `xapp-`)

## 3. Get Your Tokens

You need two tokens:

### Bot Token (`xoxb-...`)

1. In the left sidebar, go to **OAuth & Permissions**
2. Scroll down to **OAuth Tokens for Your Workspace**
3. Click **Install to Workspace** (if not already installed)
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`)

### App-Level Token (`xapp-...`)

1. Go to **Basic Information** in the left sidebar
2. Scroll to **App-Level Tokens**
3. Click **Generate Token and Scopes**
4. Add the scope `connections:write`
5. Copy the token (starts with `xapp-`)

## 4. Set Bot Permissions

Go to **OAuth & Permissions** → **Scopes** → **Bot Token Scopes** and add:

Required:
- `chat:write` — Send messages
- `app_mentions:read` — Read @mentions

Recommended:
- `commands` — For slash commands (`/adk`)
- `im:history` — Read DMs
- `channels:history` — Read public channels
- `groups:history` — Read private channels
- `mpim:history` — Read multi-party DMs

After adding scopes, **reinstall the app** to your workspace.

## 5. Install the App to Your Workspace

1. Go to **OAuth & Permissions**
2. Click **Install to Workspace** (or **Reinstall to Workspace** if updating scopes)
3. Allow the permissions
4. Copy the **Bot User OAuth Token** (`xoxb-...`)

## 6. Invite the Bot to Channels

The bot only sees messages in channels it's invited to.

### In a channel:
Type: `@YourBotName` and press Enter. Slack will ask if you want to invite the bot. Click **Invite**.

### Via slash command:
Type: `/invite @YourBotName`

### In DMs:
No invitation needed — just open a DM with the bot.

## 7. Run Your ADK Agent

### Set environment variables:

```bash
export SLACK_BOT_TOKEN="xoxb-your-bot-token-here"
export SLACK_APP_TOKEN="xapp-your-app-token-here"
export GOOGLE_API_KEY="your-google-api-key"  # or GOOGLE_GENAI_API_KEY
```

### Run the example:

```bash
# Clone and setup
git clone https://github.com/manojlds/adk-channels.git
cd adk-channels
uv venv
uv sync --all-extras --group dev

# Run the FastAPI + Slack example
uv run python examples/slack_fastapi.py
```

Or if you installed the package:
```bash
uv run adk-channels-slack
```

You should see:
```
============================================================
ADK Slack Agent Server
============================================================
Health:    http://0.0.0.0:8000/channels/health
Status:    http://0.0.0.0:8000/channels/status
Webhooks:  http://0.0.0.0:8000/channels/webhook/{adapter}
============================================================
In Slack: DM the bot, @mention it, or use /adk <msg>
============================================================
```

## 8. Test in Slack

### Direct Message (DM)
1. Open Slack
2. Find your bot in the left sidebar under "Apps"
3. Send a message: `Hello, what can you do?`
4. The bot should reply!

### @Mention in a Channel
1. Go to a channel where you invited the bot
2. Type: `@ADK Assistant what is the weather today?`
3. The bot should reply in the channel

### Slash Command
If you configured `/adk` as the slash command:
1. In any channel or DM, type: `/adk write a python function to reverse a string`
2. The bot will acknowledge with "Thinking..." and then reply

## 9. Troubleshooting

### Bot doesn't respond to DMs
- Make sure the bot is installed to your workspace (OAuth & Permissions)
- Check that `im:history` scope is granted
- Verify tokens are correct

### Bot doesn't respond to @mentions
- Invite the bot to the channel (`/invite @BotName`)
- Check that `app_mentions:read` scope is granted
- Ensure `channels:history` scope is granted

### Bot doesn't respond to slash commands
- Go to **Slash Commands** in your Slack app settings
- Create a new command: `/adk` with Request URL (not needed for Socket Mode)
- Reinstall the app after adding commands

### "Missing Slack tokens" error
- Double-check env vars are exported: `echo $SLACK_BOT_TOKEN`
- Make sure you're running from the same shell where you exported them

### Socket Mode connection issues
- Verify the app-level token starts with `xapp-` and has `connections:write` scope
- Check that Socket Mode is enabled in app settings
- Look at server logs for connection errors

### Agent returns errors
- Verify `GOOGLE_API_KEY` or `GOOGLE_GENAI_API_KEY` is set
- Check that you have access to the Gemini model
- Look at the server logs for stack traces

---

## Next Steps

- **Restrict channels**: Set `ADK_CHANNELS_ADAPTERS__SLACK__ALLOWED_CHANNEL_IDS='["C1234567890"]'`
- **Mentions only**: Set `ADK_CHANNELS_ADAPTERS__SLACK__RESPOND_TO_MENTIONS_ONLY=true`
- **Multi-app routing**: Use `MultiAppBridge` to route different channels to different agents
- **Custom runner**: Replace `agent_factory` with `agent_runner` for custom logic
