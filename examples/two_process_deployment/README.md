# Two-Process Deployment Example

This example splits Slack listening from ADK serving. `backend.py` uses ADK's official FastAPI app and endpoints; `slack_bridge.py` calls those endpoints over HTTP.

```text
Slack Socket Mode
        |
        v
slack_bridge.py, one replica
        |
        v
backend.py, scalable ADK HTTP backend
```

## Run It

Terminal 1, start the ADK backend:

```bash
uv run python examples/two_process_deployment/backend.py
```

Terminal 2, start the Slack bridge:

```bash
export SLACK_BOT_TOKEN=xoxb-your-token
export SLACK_APP_TOKEN=xapp-your-token
export ADK_BACKEND_URL=http://127.0.0.1:8001
uv run python examples/two_process_deployment/slack_bridge.py
```

Then DM the bot or mention it in Slack.

## Ports

- Backend: `ADK_BACKEND_PORT`, default `8001`
- Slack bridge: `SLACK_BRIDGE_PORT`, default `8002`
- Backend health: `GET http://127.0.0.1:8001/health`
- Bridge health: `GET http://127.0.0.1:8002/channels/health`

## Routing

By default, all Slack messages go to the backend `default` app.

Set channel IDs to route specific Slack channels:

```bash
export ADK_CHANNELS_SUPPORT_CHANNEL_ID=C012SUPPORT
export ADK_CHANNELS_ENGINEERING_CHANNEL_ID=C012ENG
```

The bridge will call ADK's session and run endpoints:

- `GET /apps/{app_name}/users/{user_id}/sessions/{session_id}`
- `POST /apps/{app_name}/users/{user_id}/sessions`
- `POST /run`

The session is created with initial state containing `channel=slack` and a `slack_thread_key`. The persistent session ID is still derived by `ChatBridge`, so each Slack thread maps to the same ADK session.

## Production Notes

- Run exactly one Slack bridge replica per Slack app token unless you add leader election.
- Scale the backend separately; it owns ADK runners and model calls.
- This local demo shares `ADK_CHANNELS_SESSION_DB` between both processes so the bridge can verify unmentioned Slack thread replies after a bridge restart.
- If the backend is remote and does not share that SQLite DB, remove `session_service_factory` from `slack_bridge.py` and require users to mention the bot once after bridge restarts.
