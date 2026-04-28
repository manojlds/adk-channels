"""Process 1: official ADK FastAPI backend.

Run this in one terminal, then run ``slack_bridge.py`` in another terminal.
"""

from __future__ import annotations

# ruff: noqa: E402, I001

import logging
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn
from dotenv import load_dotenv
from google.adk.agents.base_agent import BaseAgent
from google.adk.apps.app import App
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.cli.utils.base_agent_loader import BaseAgentLoader

from examples.agents import create_default_agent, create_engineering_agent, create_support_agent, resolve_model
from examples.session_service import resolve_session_db_path

load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("two_process_backend")


class ExampleAgentLoader(BaseAgentLoader):
    """Loads the example agents for ADK's official FastAPI server."""

    def __init__(self, *, model: str) -> None:
        self._model = model
        self._agent_names = ["default", "engineering", "support"]

    def load_agent(self, agent_name: str) -> BaseAgent | App:
        if agent_name == "support":
            return create_support_agent(model=self._model)
        if agent_name == "engineering":
            return create_engineering_agent(model=self._model)
        if agent_name == "default":
            return create_default_agent(model=self._model)
        raise ValueError(f"Unknown agent app: {agent_name}")

    def list_agents(self) -> list[str]:
        return list(self._agent_names)

    def list_agents_detailed(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "display_name": name.title(), "description": None, "type": "example"}
            for name in self._agent_names
        ]


def _sqlite_session_uri() -> str:
    db_path = resolve_session_db_path()
    if db_path.startswith(("sqlite:", "sqlite+aiosqlite:")):
        return db_path

    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


def create_app():
    """Create ADK's official FastAPI app with the example agents loaded."""
    port = int(os.environ.get("ADK_BACKEND_PORT", "8001"))
    model = resolve_model(logger=logger)
    return get_fast_api_app(
        agents_dir=str(PROJECT_ROOT / "examples"),
        agent_loader=ExampleAgentLoader(model=model),
        session_service_uri=_sqlite_session_uri(),
        artifact_service_uri="memory://",
        memory_service_uri="memory://",
        web=False,
        host="0.0.0.0",
        port=port,
        auto_create_session=False,
    )


def main() -> None:
    port = int(os.environ.get("ADK_BACKEND_PORT", "8001"))
    logger.info("Starting ADK backend on http://0.0.0.0:%d", port)
    logger.info("ADK sessions: %s", resolve_session_db_path())
    logger.info("ADK apps: GET /list-apps")
    logger.info("Create session: POST /apps/{app_name}/users/{user_id}/sessions")
    logger.info("Run endpoint: POST /run or /run_sse")
    uvicorn.run(create_app(), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
