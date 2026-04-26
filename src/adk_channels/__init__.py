"""adk-channels — Multi-channel messaging integration for Google ADK."""

from __future__ import annotations

from adk_channels.bridge import AgentFactory, AgentRunner, AppResolver, ChatBridge
from adk_channels.config import (
    AdapterConfig,
    BridgeConfig,
    ChannelsConfig,
    RouteConfig,
    SessionRule,
)
from adk_channels.interactions import ActionContext, InteractionOutcome, ToolActionRouter
from adk_channels.registry import ChannelRegistry
from adk_channels.slack_interactions import (
    TOOL_ACTION_PREFIX,
    build_tool_action_id,
    build_tool_actions_blocks,
    build_tool_button,
    parse_tool_action_id,
)
from adk_channels.tool_ui import tool_approval, tool_info, tool_multi_select, tool_single_select
from adk_channels.types import (
    AdapterDirection,
    ChannelMessage,
    IncomingMessage,
    RunResult,
)

__version__ = "0.1.0"
__all__ = [
    "ChannelRegistry",
    "ChatBridge",
    "AgentFactory",
    "AgentRunner",
    "AppResolver",
    "ChannelsConfig",
    "AdapterConfig",
    "RouteConfig",
    "SessionRule",
    "BridgeConfig",
    "IncomingMessage",
    "ChannelMessage",
    "RunResult",
    "AdapterDirection",
    "TOOL_ACTION_PREFIX",
    "build_tool_action_id",
    "parse_tool_action_id",
    "build_tool_button",
    "build_tool_actions_blocks",
    "ActionContext",
    "InteractionOutcome",
    "ToolActionRouter",
    "tool_info",
    "tool_approval",
    "tool_single_select",
    "tool_multi_select",
]
