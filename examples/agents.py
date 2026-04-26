"""Shared agent factories and demo tools for example apps."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from google.adk.agents import Agent

from adk_channels import ToolActionRouter, tool_approval, tool_info, tool_multi_select

DEFAULT_MODEL = "gemini-2.0-flash"


def resolve_model(
    *,
    logger: logging.Logger | None = None,
    default_model: str = DEFAULT_MODEL,
) -> str:
    """Resolve the model from env and set OpenAI-compatible env vars when needed."""
    model = os.environ.get("MODEL", default_model)

    openai_key = os.environ.get("OPENAI_API_KEY")
    openai_base = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")

    if openai_key and openai_base and "gemini" not in model.lower():
        os.environ.setdefault("OPENAI_API_KEY", openai_key)
        os.environ.setdefault("OPENAI_BASE_URL", openai_base)
        if logger:
            logger.info("Using OpenAI-compatible model: %s via %s", model, openai_base)
    elif logger:
        logger.info("Using model: %s", model)

    return model


FILE_CATALOG: dict[str, str] = {
    "deployment-plan.md": "Steps for rollout",
    "incident-playbook.md": "Incident response checklist",
    "customer_export.csv": "Sample customer export",
    "debug.log": "Debug output",
}
PENDING_DELETE_APPROVALS: dict[str, str] = {}
LAST_SELECTED_FILES: list[str] = []


def _resolve_file_name(query: str) -> str | None:
    candidate = query.strip().strip("`")
    if not candidate:
        return None

    if candidate in FILE_CATALOG:
        return candidate

    lowered = candidate.lower()
    for file_name in FILE_CATALOG:
        if file_name.lower() == lowered:
            return file_name

    return None


def list_internal_files() -> dict[str, Any]:
    """Tool: list dummy internal files."""
    files = sorted(FILE_CATALOG.keys())
    return tool_info("Internal files: " + ", ".join(files), files=files)


def request_delete_file(file_name: str) -> dict[str, Any]:
    """Tool: request deletion, represented as Slack approval buttons."""
    resolved = _resolve_file_name(file_name)
    if resolved is None:
        return {
            "status": "not_found",
            "message": f"File '{file_name}' was not found. Try list_internal_files first.",
        }

    request_id = f"req-{int(time.time() * 1000)}"
    PENDING_DELETE_APPROVALS[request_id] = resolved

    payload = tool_approval(
        message=f"Approval requested to delete `{resolved}`.",
        tool_name="approval",
        value={"request_id": request_id},
        actions_text=f"Approve deleting `{resolved}`?",
        block_id=f"approval_{request_id}",
    )
    payload["request_id"] = request_id
    payload["file_name"] = resolved
    return payload


def apply_delete_approval(request_id: str, decision: str) -> dict[str, Any]:
    """Tool: apply approval decision and delete dummy file if approved."""
    resolved_request_id = request_id.strip()
    file_name = PENDING_DELETE_APPROVALS.pop(resolved_request_id, None)
    if file_name is None:
        return {
            "status": "expired",
            "message": "That approval request is no longer pending.",
        }

    normalized_decision = decision.strip().lower()
    if normalized_decision == "approve":
        FILE_CATALOG.pop(file_name, None)
        return {
            "status": "approved",
            "message": f"Approved. Deleted `{file_name}`.",
            "files": sorted(FILE_CATALOG.keys()),
        }

    return {
        "status": "rejected",
        "message": f"Rejected. Kept `{file_name}`.",
        "files": sorted(FILE_CATALOG.keys()),
    }


def request_file_options() -> dict[str, Any]:
    """Tool: ask the user to choose multiple files via Slack multi-select."""
    files = sorted(FILE_CATALOG.keys())
    if not files:
        return {
            "status": "empty",
            "message": "There are no files available to choose from.",
        }

    return tool_multi_select(
        message="Waiting for multi-option selection.",
        tool_name="options",
        action="choose",
        options=files[:20],
        placeholder="Select files",
        max_selected_items=min(len(files), 5),
        actions_text="Choose one or more files.",
        block_id="file_multi_select",
    )


def apply_selected_files(selection_csv: str) -> dict[str, Any]:
    """Tool: consume selected options from Slack and store the selection."""
    selected = [value.strip() for value in selection_csv.split(",") if value.strip()]
    valid_selected = [value for value in selected if value in FILE_CATALOG]

    LAST_SELECTED_FILES.clear()
    LAST_SELECTED_FILES.extend(valid_selected)

    if not valid_selected:
        return {
            "status": "empty",
            "selected_files": [],
            "message": "No valid files selected.",
        }

    return {
        "status": "ok",
        "selected_files": valid_selected,
        "message": "Selected files: " + ", ".join(valid_selected),
    }


def create_interactive_files_agent(*, model: str) -> Agent:
    """Create the shared tool-enabled agent used by Slack examples."""
    return Agent(
        model=model,
        name="slack_assistant",
        description="A helpful assistant with approval and options tools in Slack",
        tools=[
            list_internal_files,
            request_delete_file,
            apply_delete_approval,
            request_file_options,
            apply_selected_files,
        ],
        instruction="""
You are a helpful AI assistant integrated into Slack. You help users with:
- Internal file operations on dummy data
- Safe deletion workflows that require explicit approval
- Multi-option selection workflows

You have these tools:
- list_internal_files()
- request_delete_file(file_name)
- apply_delete_approval(request_id, decision)
- request_file_options()
- apply_selected_files(selection_csv)

Rules:
1) If the user asks to list files, call list_internal_files.
2) If the user asks to delete a file, ALWAYS call request_delete_file first and wait for approval.
3) Do not claim a file is deleted until approval is applied.
4) If the user asks to pick multiple files, call request_file_options.
5) Keep responses concise and Slack-friendly.

For unrelated questions, answer normally.
        """,
    )


def create_support_agent(*, model: str) -> Agent:
    """Support agent for customer-facing queries."""
    return Agent(
        model=model,
        name="support_bot",
        description="Customer support assistant",
        instruction="""
You are a friendly customer support assistant. Help users with:
- Product questions
- Troubleshooting
- Account issues
- Billing inquiries

Be empathetic, concise, and actionable.
Use Slack markdown for formatting.
        """,
    )


def create_engineering_agent(*, model: str) -> Agent:
    """Engineering agent for technical queries."""
    return Agent(
        model=model,
        name="engineering_bot",
        description="Engineering assistant",
        instruction="""
You are an engineering assistant. Help with:
- Code review and debugging
- Architecture decisions
- Technical documentation
- Best practices

Be precise, include code examples where helpful.
Use Slack markdown (code blocks, bullets).
        """,
    )


def create_default_agent(*, model: str) -> Agent:
    """Default agent for general queries."""
    return Agent(
        model=model,
        name="general_bot",
        description="General assistant",
        instruction="""
You are a helpful general-purpose assistant.
If a question seems support-related, suggest contacting #support.
If engineering-related, suggest #engineering.
        """,
    )


def create_tool_action_router() -> ToolActionRouter:
    """Create interaction handlers for approval + options tool actions."""
    router = ToolActionRouter()

    @router.on_tool("approval", "approve")
    @router.on_tool("approval", "reject")
    def handle_approval(ctx):
        payload = ctx.action_value_json()
        request_id = str(payload.get("request_id") or ctx.action_value)
        decision = ctx.tool_action or "reject"

        result = apply_delete_approval(request_id=request_id, decision=decision)
        return str(result.get("message") or "Approval updated.")

    @router.on_tool("options", "choose")
    def handle_options(ctx):
        result = apply_selected_files(selection_csv=ctx.action_value)
        return str(result.get("message") or "Selection updated.")

    return router
