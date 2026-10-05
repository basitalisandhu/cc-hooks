"""Typed events for every Claude Code hook.

Everything in this module is derived from the hooks reference at
https://code.claude.com/docs/en/hooks as read on 2026-10-03. The ``EVENTS`` registry
carries, for each of the 33 documented events, the input fields, the field the matcher
is evaluated against, the decision pattern, the ``hookSpecificOutput`` fields and what
exit code 2 does, plus the example payload printed in the docs. Model identifiers in
the examples are replaced by placeholders such as ``<model-id>``; the structure is
unchanged.

Use ``read_event()`` at the top of a hook script and dispatch on the returned type::

    from cc_hooks import read_event, PreToolUse, deny

    event = read_event()
    if isinstance(event, PreToolUse) and event.tool_name == "Bash":
        deny("no").exit()
"""

from __future__ import annotations

import json
import ntpath
import os
import sys
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from typing import IO, Any, ClassVar

DOCS_URL = "https://code.claude.com/docs/en/hooks"
DOCS_GUIDE_URL = "https://code.claude.com/docs/en/hooks-guide"
DOCS_FETCHED = "2026-10-03"
SCHEMA_VERSION = "2026.10.03"


class HookInputError(ValueError):
    """Raised when stdin does not hold a hook payload this module can parse."""


# --------------------------------------------------------------------------- specs


@dataclass(frozen=True)
class FieldSpec:
    """One documented input field."""

    name: str
    type: str
    required: bool
    description: str


def _f(name: str, type_: str, description: str, *, required: bool = False) -> FieldSpec:
    return FieldSpec(name=name, type=type_, required=required, description=description)


@dataclass(frozen=True)
class EventSpec:
    """What the docs say about one hook event."""

    name: str
    summary: str
    matcher: str | None
    matcher_field: str | None
    matcher_values: tuple[str, ...]
    fields: tuple[FieldSpec, ...]
    decision: str
    output_fields: tuple[str, ...]
    can_block: bool
    exit_2: str
    example: dict[str, Any]

    @property
    def url(self) -> str:
        return f"{DOCS_URL}#{self.name.lower()}"

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.required)


COMMON_FIELDS: tuple[FieldSpec, ...] = (
    _f("session_id", "string", "Current session identifier", required=True),
    _f(
        "prompt_id",
        "string",
        "UUID of the user prompt being processed; absent until the first user input",
    ),
    _f("transcript_path", "string", "Path to the conversation JSON transcript"),
    _f("cwd", "string", "Working directory when the hook was invoked"),
    _f("scratchpad_dir", "string", "Session scratchpad directory; absent when there is none"),
    _f(
        "permission_mode",
        "string",
        "default, plan, acceptEdits, auto, dontAsk or bypassPermissions; not sent on every event",
    ),
    _f("effort", "object", "Object with a level field: low, medium, high, xhigh or max"),
    _f("hook_event_name", "string", "Name of the event that fired", required=True),
    _f("agent_id", "string", "Subagent identifier; present only inside a subagent"),
    _f("agent_type", "string", "Agent name; present with --agent or inside a subagent"),
)

_TOOL_FIELDS: tuple[FieldSpec, ...] = (
    _f("tool_name", "string", "Tool name; MCP tools are mcp__<server>__<tool>", required=True),
    _f("tool_input", "object", "Arguments passed to the tool", required=True),
)
_MCP_SERVER = _f(
    "mcp_server",
    "object",
    "For MCP tools: the server's name and the source its definition came from",
)
_TASK_FIELDS: tuple[FieldSpec, ...] = (
    _f("task_id", "string", "Task identifier", required=True),
    _f("task_subject", "string", "Task title", required=True),
    _f("task_description", "string", "Detailed description; may be absent"),
    _f("teammate_name", "string", "Teammate name; may be absent"),
    _f("team_name", "string", "Deprecated session-derived team name"),
)
_MODEL_SWITCH_FIELDS: tuple[FieldSpec, ...] = (
    _f("from_model", "string", "Model ID the switch changes from", required=True),
    _f("to_model", "string", "Model ID the switch changes to", required=True),
    _f("requested_model", "string|null", "Alias or ID the request named; null for the default"),
    _f("source", "string", "command, picker or sdk; PostModelSwitch adds auto and resume"),
    _f("context_tokens", "number", "Tokens the next request re-sends as its prompt"),
    _f("prompt_cache_warm", "boolean", "Whether the current prompt cache is likely warm"),
    _f("cache_ttl", "string", "Prompt cache lifetime: 5m or 1h"),
    _f("estimated_cache_write_usd", "number", "Estimated cost of re-caching the context"),
    _f("pricing", "string", "configured, catalog or default"),
)

_TRANSCRIPT = "/Users/.../.claude/projects/.../00893aaf-19fa-41d2-8238-13269b9b3ca0.jsonl"
_NOTIFICATION_TYPES = (
    "permission_prompt",
    "idle_prompt",
    "auth_success",
    "elicitation_dialog",
    "elicitation_url_dialog",
    "elicitation_complete",
    "elicitation_response",
    "agent_needs_input",
    "agent_completed",
    "quota_auto_resume_fired",
    "quota_auto_resume_stale",
    "quota_auto_resume_disabled",
)
_STOP_FAILURE_TYPES = (
    "rate_limit",
    "overloaded",
    "authentication_failed",
    "oauth_org_not_allowed",
    "account_on_hold",
    "billing_error",
    "invalid_request",
    "model_not_found",
    "server_error",
    "max_output_tokens",
    "cloud_credential_error",
    "unknown",
)

_SPECS: tuple[EventSpec, ...] = (
    EventSpec(
        name="SessionStart",
        summary="A session starts or resumes",
        matcher="how the session started",
        matcher_field="source",
        matcher_values=("startup", "resume", "clear", "compact", "fork"),
        fields=(
            _f("source", "string", "startup, resume, clear, compact or fork", required=True),
            _f("model", "string", "Active model identifier; not always included"),
            _f("session_title", "string", "Custom session title, when one is set"),
            _f(
                "seconds_since_last_response",
                "number",
                "On resume or fork: seconds since the last response",
            ),
            _f("context_tokens", "number", "On resume or fork: tokens the first request re-sends"),
            _f(
                "prompt_cache_likely_expired",
                "boolean",
                "On resume or fork: whether the cache expired",
            ),
            _f("estimated_cache_write_usd", "number", "On resume or fork: estimated re-cache cost"),
        ),
        decision="Context only (hookSpecificOutput)",
        output_fields=(
            "additionalContext",
            "initialUserMessage",
            "sessionTitle",
            "watchPaths",
            "reloadSkills",
        ),
        can_block=False,
        exit_2="Shows stderr to user only",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "hook_event_name": "SessionStart",
            "source": "resume",
            "model": "<model-id>",
            "seconds_since_last_response": 5400,
            "context_tokens": 182340,
            "prompt_cache_likely_expired": True,
            "estimated_cache_write_usd": 1.1396,
        },
    ),
    EventSpec(
        name="Setup",
        summary="claude --init-only, or -p with --init or --maintenance",
        matcher="which CLI flag triggered setup",
        matcher_field="trigger",
        matcher_values=("init", "maintenance"),
        fields=(_f("trigger", "string", "init or maintenance", required=True),),
        decision="None; JSON output is discarded",
        output_fields=(),
        can_block=False,
        exit_2="Exit code and stderr are ignored",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "hook_event_name": "Setup",
            "trigger": "init",
        },
    ),
    EventSpec(
        name="InstructionsLoaded",
        summary="A CLAUDE.md or .claude/rules/*.md file is loaded into context",
        matcher="load reason",
        matcher_field="load_reason",
        matcher_values=(
            "session_start",
            "nested_traversal",
            "path_glob_match",
            "include",
            "compact",
        ),
        fields=(
            _f("file_path", "string", "Absolute path of the instruction file", required=True),
            _f("memory_type", "string", "User, Project, Local or Managed", required=True),
            _f("load_reason", "string", "Why the file was loaded", required=True),
            _f("globs", "array", "paths: patterns; only for path_glob_match loads"),
            _f("trigger_file_path", "string", "File whose access triggered a lazy load"),
            _f("parent_file_path", "string", "Parent instruction file, for include loads"),
        ),
        decision="None",
        output_fields=(),
        can_block=False,
        exit_2="Exit code is ignored",
        example={
            "session_id": "abc123",
            "transcript_path": "/Users/.../.claude/projects/.../transcript.jsonl",
            "cwd": "/Users/my-project",
            "hook_event_name": "InstructionsLoaded",
            "file_path": "/Users/my-project/CLAUDE.md",
            "memory_type": "Project",
            "load_reason": "session_start",
        },
    ),
    EventSpec(
        name="UserPromptSubmit",
        summary="A prompt is submitted, before Claude processes it",
        matcher=None,
        matcher_field=None,
        matcher_values=(),
        fields=(
            _f("prompt", "string", "The submitted text", required=True),
            _f("session_title", "string", "Custom session title, when one is set"),
        ),
        decision="Top-level decision",
        output_fields=(
            "decision",
            "reason",
            "additionalContext",
            "sessionTitle",
            "suppressOriginalPrompt",
        ),
        can_block=True,
        exit_2="Blocks the prompt, so it never reaches Claude",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "permission_mode": "default",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Write a function to calculate the factorial of a number",
        },
    ),
    EventSpec(
        name="UserPromptExpansion",
        summary="A typed command expands into a prompt, before it reaches Claude",
        matcher="command name",
        matcher_field="command_name",
        matcher_values=(),
        fields=(
            _f("expansion_type", "string", "slash_command or mcp_prompt", required=True),
            _f("command_name", "string", "The command or skill name", required=True),
            _f("command_args", "string", "Arguments typed after the command"),
            _f("command_source", "string", "Where the command came from, such as plugin"),
            _f("prompt", "string", "The original typed prompt", required=True),
        ),
        decision="Top-level decision",
        output_fields=("decision", "reason", "additionalContext"),
        can_block=True,
        exit_2="Blocks the expansion",
        example={
            "session_id": "abc123",
            "transcript_path": "/Users/.../00893aaf.jsonl",
            "cwd": "/Users/...",
            "permission_mode": "default",
            "hook_event_name": "UserPromptExpansion",
            "expansion_type": "slash_command",
            "command_name": "example-skill",
            "command_args": "arg1 arg2",
            "command_source": "plugin",
            "prompt": "/example-skill arg1 arg2",
        },
    ),
    EventSpec(
        name="MessageDisplay",
        summary="Assistant message text is being displayed on screen",
        matcher=None,
        matcher_field=None,
        matcher_values=(),
        fields=(
            _f("turn_id", "string", "UUID of the current turn", required=True),
            _f("message_id", "string", "UUID of the assistant message", required=True),
            _f("index", "integer", "Zero-based batch index within the message", required=True),
            _f("final", "boolean", "True on the message's last batch", required=True),
            _f("delta", "string", "Newly completed lines since the prior batch", required=True),
        ),
        decision="hookSpecificOutput.displayContent (display only)",
        output_fields=("displayContent",),
        can_block=False,
        exit_2="The original text is displayed",
        example={
            "session_id": "abc123",
            "transcript_path": "/Users/.../.claude/projects/.../transcript.jsonl",
            "cwd": "/Users/my-project",
            "hook_event_name": "MessageDisplay",
            "turn_id": "0c9e6a2f-7d41-4f4e-9a15-3f4f7c2b8d10",
            "message_id": "5b2a9c8e-1f63-4d8a-b7c4-9e0d2a6f1c3b",
            "index": 0,
            "final": False,
            "delta": "Here is the plan:\n",
        },
    ),
    EventSpec(
        name="PreToolUse",
        summary="Before a tool call executes",
        matcher="tool name",
        matcher_field="tool_name",
        matcher_values=(),
        fields=(
            *_TOOL_FIELDS,
            _f("tool_use_id", "string", "Identifier of this tool call"),
            _MCP_SERVER,
        ),
        decision="hookSpecificOutput",
        output_fields=(
            "permissionDecision",
            "permissionDecisionReason",
            "updatedInput",
            "additionalContext",
        ),
        can_block=True,
        exit_2="Blocks the tool call",
        example={
            "session_id": "abc123",
            "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
            "transcript_path": "/home/user/.claude/projects/.../transcript.jsonl",
            "cwd": "/home/user/my-project",
            "scratchpad_dir": "/tmp/claude-1000/-home-user-my-project/abc123/scratchpad",
            "permission_mode": "default",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": "npm test",
                "description": "Run test suite",
                "timeout": 120000,
                "run_in_background": False,
            },
            "tool_use_id": "toolu_01ABC123...",
        },
    ),
    EventSpec(
        name="PermissionRequest",
        summary="Claude Code is about to ask for permission to use a tool",
        matcher="tool name",
        matcher_field="tool_name",
        matcher_values=(),
        fields=(
            *_TOOL_FIELDS,
            _f("permission_suggestions", "array", "Permission updates Claude Code suggests"),
            _MCP_SERVER,
        ),
        decision="hookSpecificOutput.decision",
        output_fields=(
            "decision.behavior",
            "decision.updatedInput",
            "decision.updatedPermissions",
            "decision.message",
            "decision.interrupt",
        ),
        can_block=False,
        exit_2="Not honored; the flow proceeds. Deny through the decision object instead",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "permission_mode": "default",
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {
                "command": "rm -rf node_modules",
                "description": "Remove node_modules directory",
            },
            "permission_suggestions": [
                {
                    "type": "addRules",
                    "rules": [{"toolName": "Bash", "ruleContent": "rm -rf node_modules"}],
                    "behavior": "allow",
                    "destination": "localSettings",
                }
            ],
        },
    ),
    EventSpec(
        name="PostToolUse",
        summary="After a tool call succeeds",
        matcher="tool name",
        matcher_field="tool_name",
        matcher_values=(),
        fields=(
            *_TOOL_FIELDS,
            _f("tool_response", "any", "The structured result the tool returned", required=True),
            _f("tool_use_id", "string", "Identifier of this tool call"),
            _f("duration_ms", "number", "Tool execution time in milliseconds"),
            _MCP_SERVER,
        ),
        decision="Top-level decision plus hookSpecificOutput",
        output_fields=(
            "decision",
            "reason",
            "additionalContext",
            "classifierContext",
            "updatedToolOutput",
            "updatedMCPToolOutput",
        ),
        can_block=False,
        exit_2="Shows stderr to Claude; the tool already ran",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "permission_mode": "default",
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "/path/to/file.txt", "content": "file content"},
            "tool_response": {"filePath": "/path/to/file.txt", "type": "create"},
            "tool_use_id": "toolu_01ABC123...",
            "duration_ms": 12,
        },
    ),
    EventSpec(
        name="PostToolUseFailure",
        summary="After a tool call fails",
        matcher="tool name",
        matcher_field="tool_name",
        matcher_values=(),
        fields=(
            *_TOOL_FIELDS,
            _f("tool_use_id", "string", "Identifier of this tool call"),
            _f("error", "string", "What went wrong; format depends on the tool", required=True),
            _f("is_interrupt", "boolean", "True when the failure was an abort"),
            _f("duration_ms", "number", "Tool execution time in milliseconds"),
            _MCP_SERVER,
        ),
        decision="Top-level decision plus hookSpecificOutput",
        output_fields=("decision", "reason", "additionalContext"),
        can_block=False,
        exit_2="Shows stderr to Claude; the tool already failed",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "permission_mode": "default",
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "npm test", "description": "Run test suite"},
            "tool_use_id": "toolu_01ABC123...",
            "error": "Exit code 1\nError: Cannot find module 'express'",
            "is_interrupt": False,
            "duration_ms": 4187,
        },
    ),
    EventSpec(
        name="PostToolBatch",
        summary="After a full batch of parallel tool calls resolves",
        matcher=None,
        matcher_field=None,
        matcher_values=(),
        fields=(
            _f(
                "tool_calls",
                "array",
                "Every tool call in the batch with its response",
                required=True,
            ),
        ),
        decision="Top-level decision",
        output_fields=("decision", "reason", "additionalContext"),
        can_block=True,
        exit_2="Stops the agentic loop before the next model call",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "permission_mode": "default",
            "hook_event_name": "PostToolBatch",
            "tool_calls": [
                {
                    "tool_name": "Read",
                    "tool_input": {"file_path": "/.../ledger/accounts.py"},
                    "tool_use_id": "toolu_01...",
                    "tool_response": "1\tfrom __future__ import annotations\n2\t...",
                },
                {
                    "tool_name": "Read",
                    "tool_input": {"file_path": "/.../ledger/transactions.py"},
                    "tool_use_id": "toolu_02...",
                    "tool_response": "1\tfrom __future__ import annotations\n2\t...",
                },
            ],
        },
    ),
    EventSpec(
        name="PermissionDenied",
        summary="Auto mode denies a tool call",
        matcher="tool name",
        matcher_field="tool_name",
        matcher_values=(),
        fields=(
            *_TOOL_FIELDS,
            _f("tool_use_id", "string", "Identifier of this tool call"),
            _f("reason", "string", "The denial reason", required=True),
            _MCP_SERVER,
        ),
        decision="hookSpecificOutput.retry",
        output_fields=("retry",),
        can_block=False,
        exit_2="Exit code and stderr are ignored because the denial already occurred",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "permission_mode": "auto",
            "hook_event_name": "PermissionDenied",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /tmp/build", "description": "Clean build directory"},
            "tool_use_id": "toolu_01ABC123...",
            "reason": "[Irreversible Local Destruction]",
        },
    ),
    EventSpec(
        name="Notification",
        summary="Claude Code sends a notification",
        matcher="notification type",
        matcher_field="notification_type",
        matcher_values=_NOTIFICATION_TYPES,
        fields=(
            _f("message", "string", "Notification text", required=True),
            _f("title", "string", "Notification title"),
            _f("notification_type", "string", "Which type fired", required=True),
        ),
        decision="None",
        output_fields=(),
        can_block=False,
        exit_2="Exit code and stderr are ignored",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "hook_event_name": "Notification",
            "message": "Claude needs your permission",
            "title": "Permission needed",
            "notification_type": "permission_prompt",
        },
    ),
    EventSpec(
        name="SubagentStart",
        summary="A subagent is spawned",
        matcher="agent type",
        matcher_field="agent_type",
        matcher_values=("general-purpose", "Explore", "Plan"),
        fields=(
            _f("agent_id", "string", "Unique identifier for the subagent", required=True),
            _f("agent_type", "string", "Agent name the matcher filters on", required=True),
        ),
        decision="Context only (hookSpecificOutput)",
        output_fields=("additionalContext",),
        can_block=False,
        exit_2="Shows stderr to user only",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "hook_event_name": "SubagentStart",
            "agent_id": "agent-abc123",
            "agent_type": "Explore",
        },
    ),
    EventSpec(
        name="SubagentStop",
        summary="A subagent finishes",
        matcher="agent type",
        matcher_field="agent_type",
        matcher_values=("general-purpose", "Explore", "Plan"),
        fields=(
            _f(
                "stop_hook_active", "boolean", "True when already continuing because of a stop hook"
            ),
            _f("agent_id", "string", "Unique identifier for the subagent", required=True),
            _f("agent_type", "string", "Agent name the matcher filters on", required=True),
            _f("agent_transcript_path", "string", "The subagent's own transcript"),
            _f("last_assistant_message", "string", "Text of the subagent's final response"),
            _f("background_tasks", "array", "In-flight tasks of the parent session"),
            _f("session_crons", "array", "Scheduled wakeups of the parent session"),
        ),
        decision="Top-level decision plus hookSpecificOutput.additionalContext",
        output_fields=("decision", "reason", "additionalContext"),
        can_block=True,
        exit_2="Prevents the subagent from stopping",
        example={
            "session_id": "abc123",
            "transcript_path": "~/.claude/projects/.../abc123.jsonl",
            "cwd": "/Users/...",
            "permission_mode": "default",
            "hook_event_name": "SubagentStop",
            "stop_hook_active": False,
            "agent_id": "def456",
            "agent_type": "Explore",
            "agent_transcript_path": "~/.claude/projects/.../abc123/subagents/agent-def456.jsonl",
            "last_assistant_message": "Analysis complete. Found 3 potential issues...",
            "background_tasks": [],
            "session_crons": [],
        },
    ),
    EventSpec(
        name="TaskCreated",
        summary="A task is being created via TaskCreate",
        matcher=None,
        matcher_field=None,
        matcher_values=(),
        fields=_TASK_FIELDS,
        decision="Exit code or top-level decision",
        output_fields=("decision", "reason"),
        can_block=True,
        exit_2="Rolls back the task creation",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "hook_event_name": "TaskCreated",
            "task_id": "task-001",
            "task_subject": "Implement user authentication",
            "task_description": "Add login and signup endpoints",
            "teammate_name": "implementer",
            "team_name": "session-a1b2c3d4",
        },
    ),
    EventSpec(
        name="TaskCompleted",
        summary="A task is being marked as completed",
        matcher=None,
        matcher_field=None,
        matcher_values=(),
        fields=_TASK_FIELDS,
        decision="Exit code or continue: false",
        output_fields=("continue", "stopReason"),
        can_block=True,
        exit_2="Prevents the task from being marked as completed",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "permission_mode": "default",
            "hook_event_name": "TaskCompleted",
            "task_id": "task-001",
            "task_subject": "Implement user authentication",
            "task_description": "Add login and signup endpoints",
            "teammate_name": "implementer",
            "team_name": "session-a1b2c3d4",
        },
    ),
    EventSpec(
        name="Stop",
        summary="The main agent finishes responding",
        matcher=None,
        matcher_field=None,
        matcher_values=(),
        fields=(
            _f(
                "stop_hook_active",
                "boolean",
                "True when already continuing because of a stop hook",
                required=True,
            ),
            _f("last_assistant_message", "string", "Text of Claude's final response"),
            _f("background_tasks", "array", "In-flight background tasks"),
            _f("session_crons", "array", "Session-scoped scheduled wakeups"),
        ),
        decision="Top-level decision plus hookSpecificOutput.additionalContext",
        output_fields=("decision", "reason", "additionalContext"),
        can_block=True,
        exit_2="Prevents Claude from stopping, continues the conversation",
        example={
            "session_id": "abc123",
            "transcript_path": "~/.claude/projects/.../00893aaf-19fa-41d2-8238-13269b9b3ca0.jsonl",
            "cwd": "/Users/...",
            "permission_mode": "default",
            "hook_event_name": "Stop",
            "stop_hook_active": True,
            "last_assistant_message": "I've completed the refactoring. Here's a summary...",
            "background_tasks": [
                {
                    "id": "task-001",
                    "type": "shell",
                    "status": "running",
                    "description": "tail logs",
                    "command": "tail -f /var/log/syslog",
                }
            ],
            "session_crons": [
                {
                    "id": "cron-001",
                    "schedule": "0 9 * * 1-5",
                    "recurring": True,
                    "prompt": "check the build",
                }
            ],
        },
    ),
    EventSpec(
        name="StopFailure",
        summary="The turn ends because of an API error",
        matcher="error type",
        matcher_field="error",
        matcher_values=_STOP_FAILURE_TYPES,
        fields=(
            _f("error", "string", "Error type; the matcher filters on it", required=True),
            _f("error_details", "string", "Additional details, when available"),
            _f("last_assistant_message", "string", "The rendered API error text"),
        ),
        decision="None; output and exit code are ignored except terminalSequence",
        output_fields=(),
        can_block=False,
        exit_2="Output and exit code are ignored, except terminalSequence",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "hook_event_name": "StopFailure",
            "error": "rate_limit",
            "error_details": "429 Too Many Requests",
            "last_assistant_message": "API Error: Rate limit reached",
        },
    ),
    EventSpec(
        name="TeammateIdle",
        summary="An agent team teammate is about to go idle",
        matcher=None,
        matcher_field=None,
        matcher_values=(),
        fields=(
            _f("teammate_name", "string", "Teammate about to go idle", required=True),
            _f("team_name", "string", "Deprecated session-derived team name"),
        ),
        decision="Exit code or continue: false",
        output_fields=("continue", "stopReason"),
        can_block=True,
        exit_2="Prevents the teammate from going idle, so it continues working",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "permission_mode": "default",
            "hook_event_name": "TeammateIdle",
            "teammate_name": "researcher",
            "team_name": "session-a1b2c3d4",
        },
    ),
    EventSpec(
        name="ConfigChange",
        summary="A configuration file changes during a session",
        matcher="configuration source",
        matcher_field="source",
        matcher_values=(
            "user_settings",
            "project_settings",
            "local_settings",
            "policy_settings",
            "skills",
        ),
        fields=(
            _f("source", "string", "Which configuration type changed", required=True),
            _f("file_path", "string", "Path of the file that changed"),
        ),
        decision="Top-level decision",
        output_fields=("decision", "reason"),
        can_block=True,
        exit_2="Blocks the configuration change from taking effect (except policy_settings)",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "hook_event_name": "ConfigChange",
            "source": "project_settings",
            "file_path": "/Users/.../my-project/.claude/settings.json",
        },
    ),
    EventSpec(
        name="CwdChanged",
        summary="A shell command changes the working directory",
        matcher=None,
        matcher_field=None,
        matcher_values=(),
        fields=(
            _f("old_cwd", "string", "Previous working directory", required=True),
            _f("new_cwd", "string", "New working directory", required=True),
        ),
        decision="None; watchPaths output only",
        output_fields=("watchPaths",),
        can_block=False,
        exit_2="Shows stderr to user only",
        example={
            "session_id": "abc123",
            "transcript_path": "/Users/.../.claude/projects/.../transcript.jsonl",
            "cwd": "/Users/my-project/src",
            "hook_event_name": "CwdChanged",
            "old_cwd": "/Users/my-project",
            "new_cwd": "/Users/my-project/src",
        },
    ),
    EventSpec(
        name="DirectoryAdded",
        summary="A working directory is added mid-session",
        matcher="how the directory was added",
        matcher_field="source",
        matcher_values=("slash_command", "register_repo_root"),
        fields=(
            _f("directory", "string", "Absolute path of the added directory", required=True),
            _f("source", "string", "slash_command or register_repo_root", required=True),
        ),
        decision="None",
        output_fields=(),
        can_block=False,
        exit_2="Stderr goes to the debug log; the directory is already added",
        example={
            "session_id": "abc123",
            "transcript_path": "/Users/.../.claude/projects/.../transcript.jsonl",
            "cwd": "/Users/my-project",
            "hook_event_name": "DirectoryAdded",
            "directory": "/Users/my-other-repo",
            "source": "slash_command",
        },
    ),
    EventSpec(
        name="FileChanged",
        summary="A watched file changes on disk",
        matcher="literal filenames to watch; filters on the changed file's basename",
        matcher_field="file_basename",
        matcher_values=(),
        fields=(
            _f("file_path", "string", "Absolute path of the file that changed", required=True),
            _f("event", "string", "change, add or unlink", required=True),
        ),
        decision="None; watchPaths output only",
        output_fields=("watchPaths",),
        can_block=False,
        exit_2="Shows stderr to user only",
        example={
            "session_id": "abc123",
            "transcript_path": "/Users/.../.claude/projects/.../transcript.jsonl",
            "cwd": "/Users/my-project",
            "hook_event_name": "FileChanged",
            "file_path": "/Users/my-project/.envrc",
            "event": "change",
        },
    ),
    EventSpec(
        name="WorktreeCreate",
        summary="A worktree is being created",
        matcher=None,
        matcher_field=None,
        matcher_values=(),
        fields=(_f("name", "string", "Slug identifier for the new worktree", required=True),),
        decision="Path return: command hooks print the path, HTTP hooks return worktreePath",
        output_fields=("worktreePath",),
        can_block=True,
        exit_2="Any non-zero exit code causes worktree creation to fail",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "hook_event_name": "WorktreeCreate",
            "name": "feature-auth",
        },
    ),
    EventSpec(
        name="WorktreeRemove",
        summary="A worktree is being removed",
        matcher=None,
        matcher_field=None,
        matcher_values=(),
        fields=(
            _f(
                "worktree_path",
                "string",
                "Absolute path of the worktree being removed",
                required=True,
            ),
        ),
        decision="Exit code only; JSON output is discarded",
        output_fields=(),
        can_block=True,
        exit_2="Any non-zero exit code makes removal fail if the directory still exists afterward",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "hook_event_name": "WorktreeRemove",
            "worktree_path": "/Users/.../my-project/.claude/worktrees/feature-auth",
        },
    ),
    EventSpec(
        name="PreCompact",
        summary="Before context compaction",
        matcher="what triggered compaction",
        matcher_field="trigger",
        matcher_values=("manual", "auto"),
        fields=(
            _f("trigger", "string", "manual or auto", required=True),
            _f(
                "custom_instructions",
                "string|null",
                "What the user passed to /compact; null for auto",
            ),
        ),
        decision="Top-level decision",
        output_fields=("decision", "reason"),
        can_block=True,
        exit_2="Blocks compaction",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "hook_event_name": "PreCompact",
            "trigger": "manual",
            "custom_instructions": None,
        },
    ),
    EventSpec(
        name="PostCompact",
        summary="After context compaction completes",
        matcher="what triggered compaction",
        matcher_field="trigger",
        matcher_values=("manual", "auto"),
        fields=(
            _f("trigger", "string", "manual or auto", required=True),
            _f("compact_summary", "string", "The summary the compaction generated"),
        ),
        decision="None",
        output_fields=(),
        can_block=False,
        exit_2="Shows stderr to user only",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "hook_event_name": "PostCompact",
            "trigger": "manual",
            "compact_summary": "Summary of the compacted conversation...",
        },
    ),
    EventSpec(
        name="PreModelSwitch",
        summary="Before a requested model switch is applied",
        matcher="canonical name of the model the session switches to, derived from to_model",
        matcher_field="to_model",
        matcher_values=(),
        fields=_MODEL_SWITCH_FIELDS,
        decision="hookSpecificOutput or top-level decision",
        output_fields=("permissionDecision", "permissionDecisionReason", "decision", "reason"),
        can_block=True,
        exit_2="Blocks the model switch and shows stderr to the user",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "hook_event_name": "PreModelSwitch",
            "from_model": "<model-a>",
            "to_model": "<model-b>",
            "requested_model": "<alias>",
            "source": "command",
            "context_tokens": 182340,
            "prompt_cache_warm": True,
            "cache_ttl": "5m",
            "estimated_cache_write_usd": 1.1396,
            "pricing": "catalog",
        },
    ),
    EventSpec(
        name="PostModelSwitch",
        summary="After the session's model changes",
        matcher="canonical name of the model the session switched to, derived from to_model",
        matcher_field="to_model",
        matcher_values=(),
        fields=_MODEL_SWITCH_FIELDS,
        decision="Context only (hookSpecificOutput)",
        output_fields=("additionalContext",),
        can_block=False,
        exit_2="Shows stderr to user only; the model already switched",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "hook_event_name": "PostModelSwitch",
            "from_model": "<model-a>",
            "to_model": "<model-b>",
            "requested_model": None,
            "source": "auto",
            "context_tokens": 182340,
            "prompt_cache_warm": True,
            "cache_ttl": "5m",
            "estimated_cache_write_usd": 1.1396,
            "pricing": "catalog",
        },
    ),
    EventSpec(
        name="SessionEnd",
        summary="A session ends",
        matcher="why the session ended",
        matcher_field="reason",
        matcher_values=("clear", "resume", "logout", "prompt_input_exit", "other"),
        fields=(_f("reason", "string", "Why the session ended", required=True),),
        decision="None; JSON output is discarded",
        output_fields=(),
        can_block=False,
        exit_2="Shows stderr to user only",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "hook_event_name": "SessionEnd",
            "reason": "other",
        },
    ),
    EventSpec(
        name="Elicitation",
        summary="An MCP server requests user input",
        matcher="MCP server name",
        matcher_field="mcp_server_name",
        matcher_values=(),
        fields=(
            _f("mcp_server_name", "string", "The requesting MCP server", required=True),
            _f("message", "string", "The server's message", required=True),
            _f("mode", "string", "form or url"),
            _f("url", "string", "URL to open, for url mode"),
            _f("elicitation_id", "string", "Identifier of the elicitation"),
            _f("requested_schema", "object", "JSON schema of the form, for form mode"),
        ),
        decision="hookSpecificOutput",
        output_fields=("action", "content"),
        can_block=True,
        exit_2="Denies the elicitation",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "hook_event_name": "Elicitation",
            "mcp_server_name": "my-mcp-server",
            "message": "Please provide your credentials",
            "mode": "form",
            "requested_schema": {
                "type": "object",
                "properties": {"username": {"type": "string", "title": "Username"}},
            },
        },
    ),
    EventSpec(
        name="ElicitationResult",
        summary="After the user responds to an MCP elicitation",
        matcher="MCP server name",
        matcher_field="mcp_server_name",
        matcher_values=(),
        fields=(
            _f("mcp_server_name", "string", "The requesting MCP server", required=True),
            _f("action", "string", "accept, decline or cancel", required=True),
            _f("mode", "string", "form or url"),
            _f("elicitation_id", "string", "Identifier of the elicitation"),
            _f("content", "object", "Submitted form values"),
        ),
        decision="hookSpecificOutput",
        output_fields=("action", "content"),
        can_block=True,
        exit_2="Blocks the response (action becomes decline)",
        example={
            "session_id": "abc123",
            "transcript_path": _TRANSCRIPT,
            "cwd": "/Users/...",
            "hook_event_name": "ElicitationResult",
            "mcp_server_name": "my-mcp-server",
            "action": "accept",
            "content": {"username": "alice"},
            "mode": "form",
            "elicitation_id": "elicit-123",
        },
    ),
)

EVENTS: dict[str, EventSpec] = {spec.name: spec for spec in _SPECS}
EVENT_NAMES: tuple[str, ...] = tuple(EVENTS)
TOOL_EVENTS: frozenset[str] = frozenset(
    {"PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest", "PermissionDenied"}
)

# tool_input fields of the built-in tools, from the PreToolUse section of the reference
TOOL_INPUT_FIELDS: dict[str, tuple[FieldSpec, ...]] = {
    "Bash": (
        _f("command", "string", "The shell command to execute", required=True),
        _f("description", "string", "Optional description of the command"),
        _f("timeout", "number", "Optional timeout in milliseconds"),
        _f("run_in_background", "boolean", "Whether to run in the background"),
    ),
    "PowerShell": (
        _f("command", "string", "The PowerShell command to execute", required=True),
        _f("description", "string", "Optional description of the command"),
        _f("timeout", "number", "Optional timeout in milliseconds"),
        _f("run_in_background", "boolean", "Whether to run in the background"),
    ),
    "Write": (
        _f("file_path", "string", "Absolute path to write", required=True),
        _f("content", "string", "Content to write", required=True),
    ),
    "Edit": (
        _f("file_path", "string", "Absolute path to edit", required=True),
        _f("old_string", "string", "Text to find", required=True),
        _f("new_string", "string", "Replacement text", required=True),
        _f("replace_all", "boolean", "Replace every occurrence"),
    ),
    "Read": (
        _f("file_path", "string", "Absolute path to read", required=True),
        _f("offset", "number", "Line to start from"),
        _f("limit", "number", "Number of lines"),
    ),
    "Glob": (
        _f("pattern", "string", "Glob pattern", required=True),
        _f("path", "string", "Directory to search"),
    ),
    "Grep": (
        _f("pattern", "string", "Regular expression", required=True),
        _f("path", "string", "File or directory to search"),
        _f("glob", "string", "Glob filter"),
        _f("output_mode", "string", "content, files_with_matches or count"),
        _f("-i", "boolean", "Case insensitive"),
        _f("multiline", "boolean", "Multiline matching"),
    ),
    "WebFetch": (
        _f("url", "string", "URL to fetch", required=True),
        _f("prompt", "string", "Prompt to run on the content", required=True),
    ),
    "WebSearch": (
        _f("query", "string", "Search query", required=True),
        _f("allowed_domains", "array", "Only these domains"),
        _f("blocked_domains", "array", "Exclude these domains"),
    ),
    "Agent": (
        _f("prompt", "string", "The task for the subagent", required=True),
        _f("description", "string", "Short description of the task"),
        _f("subagent_type", "string", "Type of subagent"),
        _f("model", "string", "Optional model alias override"),
    ),
    "AskUserQuestion": (
        _f("questions", "array", "Questions to present", required=True),
        _f("answers", "object", "Answers supplied through updatedInput"),
    ),
    "ExitPlanMode": (
        _f("plan", "string", "Plan content in Markdown, injected from disk"),
        _f("planFilePath", "string", "Path to the plan file, injected"),
        _f("allowedPrompts", "array", "Deprecated; accepted but ignored"),
    ),
}


# --------------------------------------------------------------------------- events


@dataclass(frozen=True, kw_only=True)
class Event:
    """Fields every hook event receives. Unknown fields land in ``extra``."""

    EVENT_NAME: ClassVar[str] = ""

    hook_event_name: str
    session_id: str
    prompt_id: str | None = None
    transcript_path: str | None = None
    cwd: str | None = None
    scratchpad_dir: str | None = None
    permission_mode: str | None = None
    effort: dict[str, Any] | None = None
    agent_id: str | None = None
    agent_type: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def spec(self) -> EventSpec:
        return EVENTS[self.hook_event_name]

    @property
    def matcher_value(self) -> str | None:
        """The value Claude Code evaluates this event's matcher against, or None."""
        matcher_field = self.spec.matcher_field
        if matcher_field is None:
            return None
        if matcher_field == "file_basename":
            path = getattr(self, "file_path", None)
            if not isinstance(path, str):
                return None
            basename = ntpath.basename if "\\" in path and "/" not in path else os.path.basename
            return basename(path)
        value = getattr(self, matcher_field, None)
        if matcher_field == "to_model" and isinstance(value, str):
            return value.removesuffix("[1m]").strip()
        return value if isinstance(value, str) else None

    @property
    def in_subagent(self) -> bool:
        return self.agent_id is not None

    def to_dict(self) -> dict[str, Any]:
        """The payload as a dict. Fields that are None are omitted; extras are merged back."""
        data: dict[str, Any] = {}
        for f in dataclass_fields(self):
            if f.name == "extra":
                continue
            value = getattr(self, f.name)
            if value is not None:
                data[f.name] = value
        data.update(self.extra)
        return data

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)


@dataclass(frozen=True, kw_only=True)
class ToolEvent(Event):
    """Shared by the five tool events."""

    tool_name: str
    tool_input: dict[str, Any]
    mcp_server: dict[str, Any] | None = None

    @property
    def is_mcp_tool(self) -> bool:
        return self.tool_name.startswith("mcp__")

    @property
    def command(self) -> str | None:
        """The shell command for Bash and PowerShell calls, else None."""
        value = self.tool_input.get("command")
        return value if isinstance(value, str) else None

    @property
    def file_path(self) -> str | None:
        """tool_input.file_path for the file tools, else None."""
        value = self.tool_input.get("file_path")
        return value if isinstance(value, str) else None


@dataclass(frozen=True, kw_only=True)
class SessionStart(Event):
    EVENT_NAME: ClassVar[str] = "SessionStart"
    hook_event_name: str = "SessionStart"
    source: str
    model: str | None = None
    session_title: str | None = None
    seconds_since_last_response: float | None = None
    context_tokens: float | None = None
    prompt_cache_likely_expired: bool | None = None
    estimated_cache_write_usd: float | None = None


@dataclass(frozen=True, kw_only=True)
class Setup(Event):
    EVENT_NAME: ClassVar[str] = "Setup"
    hook_event_name: str = "Setup"
    trigger: str


@dataclass(frozen=True, kw_only=True)
class InstructionsLoaded(Event):
    EVENT_NAME: ClassVar[str] = "InstructionsLoaded"
    hook_event_name: str = "InstructionsLoaded"
    file_path: str
    memory_type: str
    load_reason: str
    globs: list[str] | None = None
    trigger_file_path: str | None = None
    parent_file_path: str | None = None


@dataclass(frozen=True, kw_only=True)
class UserPromptSubmit(Event):
    EVENT_NAME: ClassVar[str] = "UserPromptSubmit"
    hook_event_name: str = "UserPromptSubmit"
    prompt: str
    session_title: str | None = None


@dataclass(frozen=True, kw_only=True)
class UserPromptExpansion(Event):
    EVENT_NAME: ClassVar[str] = "UserPromptExpansion"
    hook_event_name: str = "UserPromptExpansion"
    expansion_type: str
    command_name: str
    prompt: str
    command_args: str | None = None
    command_source: str | None = None


@dataclass(frozen=True, kw_only=True)
class MessageDisplay(Event):
    EVENT_NAME: ClassVar[str] = "MessageDisplay"
    hook_event_name: str = "MessageDisplay"
    turn_id: str
    message_id: str
    index: int
    final: bool
    delta: str


@dataclass(frozen=True, kw_only=True)
class PreToolUse(ToolEvent):
    EVENT_NAME: ClassVar[str] = "PreToolUse"
    hook_event_name: str = "PreToolUse"
    tool_use_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class PermissionRequest(ToolEvent):
    EVENT_NAME: ClassVar[str] = "PermissionRequest"
    hook_event_name: str = "PermissionRequest"
    permission_suggestions: list[dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class PostToolUse(ToolEvent):
    EVENT_NAME: ClassVar[str] = "PostToolUse"
    hook_event_name: str = "PostToolUse"
    tool_response: Any
    tool_use_id: str | None = None
    duration_ms: float | None = None


@dataclass(frozen=True, kw_only=True)
class PostToolUseFailure(ToolEvent):
    EVENT_NAME: ClassVar[str] = "PostToolUseFailure"
    hook_event_name: str = "PostToolUseFailure"
    error: str
    tool_use_id: str | None = None
    is_interrupt: bool | None = None
    duration_ms: float | None = None


@dataclass(frozen=True, kw_only=True)
class PostToolBatch(Event):
    EVENT_NAME: ClassVar[str] = "PostToolBatch"
    hook_event_name: str = "PostToolBatch"
    tool_calls: list[dict[str, Any]]


@dataclass(frozen=True, kw_only=True)
class PermissionDenied(ToolEvent):
    EVENT_NAME: ClassVar[str] = "PermissionDenied"
    hook_event_name: str = "PermissionDenied"
    reason: str
    tool_use_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class Notification(Event):
    EVENT_NAME: ClassVar[str] = "Notification"
    hook_event_name: str = "Notification"
    message: str
    notification_type: str
    title: str | None = None


@dataclass(frozen=True, kw_only=True)
class SubagentStart(Event):
    EVENT_NAME: ClassVar[str] = "SubagentStart"
    hook_event_name: str = "SubagentStart"
    # field() with no default: a plain redeclaration would inherit the base class default
    agent_id: str = field()
    agent_type: str = field()


@dataclass(frozen=True, kw_only=True)
class SubagentStop(Event):
    EVENT_NAME: ClassVar[str] = "SubagentStop"
    hook_event_name: str = "SubagentStop"
    # field() with no default: a plain redeclaration would inherit the base class default
    agent_id: str = field()
    agent_type: str = field()
    stop_hook_active: bool | None = None
    agent_transcript_path: str | None = None
    last_assistant_message: str | None = None
    background_tasks: list[dict[str, Any]] | None = None
    session_crons: list[dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class TaskCreated(Event):
    EVENT_NAME: ClassVar[str] = "TaskCreated"
    hook_event_name: str = "TaskCreated"
    task_id: str
    task_subject: str
    task_description: str | None = None
    teammate_name: str | None = None
    team_name: str | None = None


@dataclass(frozen=True, kw_only=True)
class TaskCompleted(Event):
    EVENT_NAME: ClassVar[str] = "TaskCompleted"
    hook_event_name: str = "TaskCompleted"
    task_id: str
    task_subject: str
    task_description: str | None = None
    teammate_name: str | None = None
    team_name: str | None = None


@dataclass(frozen=True, kw_only=True)
class Stop(Event):
    EVENT_NAME: ClassVar[str] = "Stop"
    hook_event_name: str = "Stop"
    stop_hook_active: bool
    last_assistant_message: str | None = None
    background_tasks: list[dict[str, Any]] | None = None
    session_crons: list[dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class StopFailure(Event):
    EVENT_NAME: ClassVar[str] = "StopFailure"
    hook_event_name: str = "StopFailure"
    error: str
    error_details: str | None = None
    last_assistant_message: str | None = None


@dataclass(frozen=True, kw_only=True)
class TeammateIdle(Event):
    EVENT_NAME: ClassVar[str] = "TeammateIdle"
    hook_event_name: str = "TeammateIdle"
    teammate_name: str
    team_name: str | None = None


@dataclass(frozen=True, kw_only=True)
class ConfigChange(Event):
    EVENT_NAME: ClassVar[str] = "ConfigChange"
    hook_event_name: str = "ConfigChange"
    source: str
    file_path: str | None = None


@dataclass(frozen=True, kw_only=True)
class CwdChanged(Event):
    EVENT_NAME: ClassVar[str] = "CwdChanged"
    hook_event_name: str = "CwdChanged"
    old_cwd: str
    new_cwd: str


@dataclass(frozen=True, kw_only=True)
class DirectoryAdded(Event):
    EVENT_NAME: ClassVar[str] = "DirectoryAdded"
    hook_event_name: str = "DirectoryAdded"
    directory: str
    source: str


@dataclass(frozen=True, kw_only=True)
class FileChanged(Event):
    EVENT_NAME: ClassVar[str] = "FileChanged"
    hook_event_name: str = "FileChanged"
    file_path: str
    event: str


@dataclass(frozen=True, kw_only=True)
class WorktreeCreate(Event):
    EVENT_NAME: ClassVar[str] = "WorktreeCreate"
    hook_event_name: str = "WorktreeCreate"
    name: str


@dataclass(frozen=True, kw_only=True)
class WorktreeRemove(Event):
    EVENT_NAME: ClassVar[str] = "WorktreeRemove"
    hook_event_name: str = "WorktreeRemove"
    worktree_path: str


@dataclass(frozen=True, kw_only=True)
class PreCompact(Event):
    EVENT_NAME: ClassVar[str] = "PreCompact"
    hook_event_name: str = "PreCompact"
    trigger: str
    custom_instructions: str | None = None


@dataclass(frozen=True, kw_only=True)
class PostCompact(Event):
    EVENT_NAME: ClassVar[str] = "PostCompact"
    hook_event_name: str = "PostCompact"
    trigger: str
    compact_summary: str | None = None


@dataclass(frozen=True, kw_only=True)
class ModelSwitchEvent(Event):
    """Shared by PreModelSwitch and PostModelSwitch."""

    from_model: str
    to_model: str
    requested_model: str | None = None
    source: str | None = None
    context_tokens: float | None = None
    prompt_cache_warm: bool | None = None
    cache_ttl: str | None = None
    estimated_cache_write_usd: float | None = None
    pricing: str | None = None


@dataclass(frozen=True, kw_only=True)
class PreModelSwitch(ModelSwitchEvent):
    EVENT_NAME: ClassVar[str] = "PreModelSwitch"
    hook_event_name: str = "PreModelSwitch"


@dataclass(frozen=True, kw_only=True)
class PostModelSwitch(ModelSwitchEvent):
    EVENT_NAME: ClassVar[str] = "PostModelSwitch"
    hook_event_name: str = "PostModelSwitch"


@dataclass(frozen=True, kw_only=True)
class SessionEnd(Event):
    EVENT_NAME: ClassVar[str] = "SessionEnd"
    hook_event_name: str = "SessionEnd"
    reason: str


@dataclass(frozen=True, kw_only=True)
class Elicitation(Event):
    EVENT_NAME: ClassVar[str] = "Elicitation"
    hook_event_name: str = "Elicitation"
    mcp_server_name: str
    message: str
    mode: str | None = None
    url: str | None = None
    elicitation_id: str | None = None
    requested_schema: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class ElicitationResult(Event):
    EVENT_NAME: ClassVar[str] = "ElicitationResult"
    hook_event_name: str = "ElicitationResult"
    mcp_server_name: str
    action: str
    mode: str | None = None
    elicitation_id: str | None = None
    content: dict[str, Any] | None = None


EVENT_CLASSES: dict[str, type[Event]] = {
    cls.EVENT_NAME: cls
    for cls in (
        SessionStart,
        Setup,
        InstructionsLoaded,
        UserPromptSubmit,
        UserPromptExpansion,
        MessageDisplay,
        PreToolUse,
        PermissionRequest,
        PostToolUse,
        PostToolUseFailure,
        PostToolBatch,
        PermissionDenied,
        Notification,
        SubagentStart,
        SubagentStop,
        TaskCreated,
        TaskCompleted,
        Stop,
        StopFailure,
        TeammateIdle,
        ConfigChange,
        CwdChanged,
        DirectoryAdded,
        FileChanged,
        WorktreeCreate,
        WorktreeRemove,
        PreCompact,
        PostCompact,
        PreModelSwitch,
        PostModelSwitch,
        SessionEnd,
        Elicitation,
        ElicitationResult,
    )
}


# --------------------------------------------------------------------------- parsing

_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
    "any": lambda v: True,
}


def _type_ok(value: Any, type_spec: str) -> bool:
    return any(_TYPE_CHECKS[name](value) for name in type_spec.split("|"))


def parse_event(data: Any, *, strict: bool = True) -> Event:
    """Build the typed event for a decoded hook payload.

    ``strict`` checks the documented required fields and the types of the fields that
    are present. With ``strict=False`` only ``hook_event_name`` and ``session_id`` are
    checked and a missing documented field reads as None, which is what you want in a
    hook that must keep working across Claude Code versions. Unknown fields are kept in
    ``event.extra`` either way.
    """
    if not isinstance(data, dict):
        raise HookInputError(f"hook input must be a JSON object, got {type(data).__name__}")
    name = data.get("hook_event_name")
    if not isinstance(name, str) or not name:
        raise HookInputError("hook input has no hook_event_name")
    spec = EVENTS.get(name)
    if spec is None:
        raise HookInputError(
            f"unknown hook event {name!r}; this build knows {len(EVENTS)} events from {DOCS_URL}"
        )
    known = {f.name: f for f in (*COMMON_FIELDS, *spec.fields)}
    required = ["session_id"] + (list(spec.required_fields) if strict else [])
    for field_name in required:
        if field_name not in data:
            raise HookInputError(f"{name}: missing required field {field_name!r}")
    if strict:
        for field_name, value in data.items():
            field_spec = known.get(field_name)
            if field_spec is None or value is None:
                continue
            if not _type_ok(value, field_spec.type):
                raise HookInputError(
                    f"{name}: field {field_name!r} should be {field_spec.type}, "
                    f"got {type(value).__name__}"
                )
    kwargs = {key: value for key, value in data.items() if key in known}
    if not strict:
        for field_name in spec.required_fields:
            kwargs.setdefault(field_name, None)
    extra = {key: value for key, value in data.items() if key not in known}
    cls = EVENT_CLASSES[name]
    try:
        return cls(**kwargs, extra=extra)
    except TypeError as exc:  # a required field present with the wrong shape in lenient mode
        raise HookInputError(f"{name}: {exc}") from exc


def read_event(stream: IO[str] | None = None, *, strict: bool = True) -> Event:
    """Read one hook payload from ``stream`` (default stdin) and return the typed event.

    Raises ``HookInputError`` on empty input, invalid JSON, an unknown event or (with
    ``strict``) a missing required field.
    """
    text = (stream if stream is not None else sys.stdin).read()
    if not text.strip():
        raise HookInputError("empty hook input")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HookInputError(f"hook input is not valid JSON: {exc}") from exc
    return parse_event(data, strict=strict)


def dry_run() -> bool:
    """True when ``cc-hooks test`` runs this hook with ``CC_HOOKS_DRY_RUN=1``.

    Hooks with side effects (writing logs, calling services) should check this and skip
    the side effect, so fixtures can be replayed safely.
    """
    return os.environ.get("CC_HOOKS_DRY_RUN", "") not in ("", "0")
