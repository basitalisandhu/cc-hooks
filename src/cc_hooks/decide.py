"""Decision builders that print exactly the JSON shapes the hooks reference documents.

Every builder returns a ``Decision``. ``decision.exit()`` writes the JSON to stdout (or
the reason to stderr for an exit-code-2 block) and exits with the right code: 0 for JSON
decisions, 2 for blocking errors. Builders refuse combinations the docs do not list, for
example ``update_output()`` on ``PreToolUse`` or ``defer()`` on ``PreModelSwitch``.

Source: https://code.claude.com/docs/en/hooks, sections "Exit code output", "JSON output"
and "Decision control", as read on 2026-10-03.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import IO, Any, NoReturn

from .events import EVENTS

__all__ = [
    "CONTEXT_EVENTS",
    "CONTINUE_IGNORED",
    "EXIT_2_BLOCK_EVENTS",
    "PERMISSION_DECISIONS",
    "PLAIN_TEXT_CONTEXT_EVENTS",
    "SYSTEM_MESSAGE_IGNORED",
    "TOP_LEVEL_BLOCK_EVENTS",
    "WATCH_PATH_EVENTS",
    "Decision",
    "DecisionError",
    "Observed",
    "add_context",
    "allow",
    "ask",
    "block",
    "blocking_error",
    "combine",
    "defer",
    "deny",
    "display",
    "elicitation",
    "no_decision",
    "observe",
    "permission_request",
    "retry",
    "session_start",
    "stop",
    "system_message",
    "update_input",
    "update_output",
    "watch_paths",
]


class DecisionError(ValueError):
    """Raised for an output shape the docs do not allow on the given event."""


# Which permissionDecision values each event accepts, most restrictive first. This is the
# precedence Claude Code applies when several hooks answer: deny > defer > ask > allow.
PERMISSION_DECISIONS: dict[str, tuple[str, ...]] = {
    "PreToolUse": ("deny", "defer", "ask", "allow"),
    "PreModelSwitch": ("deny", "ask", "allow"),
}

# Events whose block is a top-level {"decision": "block", "reason": ...} object.
TOP_LEVEL_BLOCK_EVENTS: frozenset[str] = frozenset(
    {
        "UserPromptSubmit",
        "UserPromptExpansion",
        "PostToolUse",
        "PostToolUseFailure",
        "PostToolBatch",
        "Stop",
        "SubagentStop",
        "ConfigChange",
        "PreCompact",
        "TaskCreated",
        "PreModelSwitch",
    }
)

# Events where exit code 2 blocks ("Can block? Yes" in the per-event table).
EXIT_2_BLOCK_EVENTS: frozenset[str] = frozenset(
    {
        "PreToolUse",
        "UserPromptSubmit",
        "UserPromptExpansion",
        "Stop",
        "SubagentStop",
        "TeammateIdle",
        "TaskCreated",
        "TaskCompleted",
        "ConfigChange",
        "PostToolBatch",
        "PreCompact",
        "PreModelSwitch",
        "Elicitation",
        "ElicitationResult",
        "WorktreeCreate",
        "WorktreeRemove",
    }
)

# Events that accept hookSpecificOutput.additionalContext.
CONTEXT_EVENTS: frozenset[str] = frozenset(
    {
        "SessionStart",
        "SubagentStart",
        "UserPromptSubmit",
        "UserPromptExpansion",
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "PostToolBatch",
        "Stop",
        "SubagentStop",
        "PostModelSwitch",
    }
)

# Events where plain-text stdout on exit 0 is added to Claude's context.
PLAIN_TEXT_CONTEXT_EVENTS: frozenset[str] = frozenset(
    {"UserPromptSubmit", "UserPromptExpansion", "SessionStart", "PostModelSwitch"}
)

# Events whose section says "continue" is discarded or ignored.
CONTINUE_IGNORED: frozenset[str] = frozenset(
    {
        "Setup",
        "InstructionsLoaded",
        "MessageDisplay",
        "Notification",
        "TaskCreated",
        "ConfigChange",
        "CwdChanged",
        "DirectoryAdded",
        "FileChanged",
        "WorktreeCreate",
        "WorktreeRemove",
        "PreCompact",
        "PostCompact",
        "SessionEnd",
        "Elicitation",
        "ElicitationResult",
        "StopFailure",
    }
)

# Events whose section says systemMessage is discarded.
SYSTEM_MESSAGE_IGNORED: frozenset[str] = frozenset(
    {
        "Setup",
        "InstructionsLoaded",
        "MessageDisplay",
        "Notification",
        "ConfigChange",
        "WorktreeCreate",
        "WorktreeRemove",
        "PreCompact",
        "PostCompact",
        "SessionEnd",
        "Elicitation",
        "ElicitationResult",
        "StopFailure",
    }
)

WATCH_PATH_EVENTS: frozenset[str] = frozenset({"SessionStart", "CwdChanged", "FileChanged"})
ELICITATION_EVENTS: frozenset[str] = frozenset({"Elicitation", "ElicitationResult"})
ELICITATION_ACTIONS: tuple[str, ...] = ("accept", "decline", "cancel")


@dataclass(frozen=True)
class Decision:
    """What a hook will print and how it will exit.

    ``kind`` is a short label used by ``cc-hooks test`` expectations: allow, deny, ask,
    defer, block, stop, context, message, retry, display, accept, decline, cancel, watch
    or none.
    """

    kind: str
    event: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    exit_code: int = 0
    stderr: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.payload))

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.payload, indent=indent)

    @property
    def reason(self) -> str | None:
        """The human-readable reason, wherever this decision's shape keeps it."""
        if self.stderr:
            return self.stderr
        hso = self.payload.get("hookSpecificOutput", {})
        for key in ("permissionDecisionReason",):
            if isinstance(hso.get(key), str):
                return hso[key]
        inner = hso.get("decision")
        if isinstance(inner, dict) and isinstance(inner.get("message"), str):
            return inner["message"]
        for key in ("reason", "stopReason"):
            if isinstance(self.payload.get(key), str):
                return self.payload[key]
        return None

    def with_fields(
        self,
        *,
        system_message: str | None = None,
        terminal_sequence: str | None = None,
        continue_: bool | None = None,
        stop_reason: str | None = None,
    ) -> Decision:
        """Return a copy with universal top-level fields added."""
        payload = self.to_dict()
        if system_message is not None:
            if self.event in SYSTEM_MESSAGE_IGNORED:
                raise DecisionError(f"{self.event} discards systemMessage")
            payload["systemMessage"] = system_message
        if terminal_sequence is not None:
            payload["terminalSequence"] = terminal_sequence
        if continue_ is not None:
            if self.event in CONTINUE_IGNORED:
                raise DecisionError(f"{self.event} ignores the continue field")
            payload["continue"] = continue_
        if stop_reason is not None:
            payload["stopReason"] = stop_reason
        return replace(self, payload=payload)

    def emit(self, stdout: IO[str] | None = None, stderr: IO[str] | None = None) -> int:
        """Write the decision and return the exit code without exiting."""
        out = stdout if stdout is not None else sys.stdout
        err = stderr if stderr is not None else sys.stderr
        if self.payload:
            out.write(self.to_json() + "\n")
            out.flush()
        if self.stderr:
            err.write(self.stderr.rstrip("\n") + "\n")
            err.flush()
        return self.exit_code

    def exit(self) -> NoReturn:
        """Write the decision and exit the hook with the right code."""
        sys.exit(self.emit())


def _check_event(event: str, allowed: Iterable[str], what: str) -> None:
    if event not in EVENTS:
        raise DecisionError(f"unknown hook event {event!r}")
    allowed_set = set(allowed)
    if event not in allowed_set:
        names = ", ".join(sorted(allowed_set))
        raise DecisionError(f"{what} is not documented for {event}; allowed on: {names}")


def _hso(event: str, **fields: Any) -> dict[str, Any]:
    inner: dict[str, Any] = {"hookEventName": event}
    inner.update({key: value for key, value in fields.items() if value is not None})
    return {"hookSpecificOutput": inner}


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DecisionError(f"{what} must be a non-empty string")
    return value


# --------------------------------------------------------------------------- builders


def allow(
    reason: str | None = None,
    updated_input: dict[str, Any] | None = None,
    additional_context: str | None = None,
    *,
    event: str = "PreToolUse",
) -> Decision:
    """permissionDecision "allow" (PreToolUse, PreModelSwitch).

    On PreToolUse the optional ``updated_input`` replaces the whole tool input and
    ``additional_context`` is added next to the tool result.
    """
    _check_event(event, PERMISSION_DECISIONS, "allow()")
    if event != "PreToolUse" and (updated_input is not None or additional_context is not None):
        raise DecisionError(f"{event} accepts neither updatedInput nor additionalContext")
    if updated_input is not None and not isinstance(updated_input, dict):
        raise DecisionError("updated_input must be a dict (it replaces the whole tool input)")
    payload = _hso(
        event,
        permissionDecision="allow",
        permissionDecisionReason=reason,
        updatedInput=updated_input,
        additionalContext=additional_context,
    )
    return Decision("allow", event, payload)


def deny(reason: str, *, event: str = "PreToolUse") -> Decision:
    """permissionDecision "deny" with the reason Claude sees (PreToolUse, PreModelSwitch)."""
    _check_event(event, PERMISSION_DECISIONS, "deny()")
    payload = _hso(
        event, permissionDecision="deny", permissionDecisionReason=_text(reason, "reason")
    )
    return Decision("deny", event, payload)


def ask(reason: str | None = None, *, event: str = "PreToolUse") -> Decision:
    """permissionDecision "ask": show the permission prompt with the reason."""
    _check_event(event, PERMISSION_DECISIONS, "ask()")
    payload = _hso(event, permissionDecision="ask", permissionDecisionReason=reason)
    return Decision("ask", event, payload)


def defer(*, event: str = "PreToolUse") -> Decision:
    """permissionDecision "defer": pause the tool call for an Agent SDK host (PreToolUse only)."""
    _check_event(event, ("PreToolUse",), "defer()")
    return Decision("defer", event, _hso(event, permissionDecision="defer"))


def update_input(
    updated_input: dict[str, Any],
    *,
    decision: str = "allow",
    reason: str | None = None,
    event: str = "PreToolUse",
) -> Decision:
    """Replace the tool input before it runs, paired with "allow" or "ask" (PreToolUse)."""
    _check_event(event, ("PreToolUse",), "update_input()")
    if decision not in ("allow", "ask"):
        raise DecisionError('updatedInput pairs with "allow" or "ask"; it is ignored for "defer"')
    if not isinstance(updated_input, dict):
        raise DecisionError("updated_input must be a dict (it replaces the whole tool input)")
    payload = _hso(
        event,
        permissionDecision=decision,
        permissionDecisionReason=reason,
        updatedInput=updated_input,
    )
    return Decision(decision, event, payload)


def update_output(
    updated_tool_output: Any,
    additional_context: str | None = None,
    *,
    mcp: bool = False,
    event: str = "PostToolUse",
) -> Decision:
    """Replace what Claude sees as the tool result (PostToolUse only).

    The value must match the tool's output shape; for built-in tools a mismatch is
    ignored and the original output is used. ``mcp=True`` writes ``updatedMCPToolOutput``
    instead, which only applies to MCP tools.
    """
    _check_event(event, ("PostToolUse",), "update_output()")
    key = "updatedMCPToolOutput" if mcp else "updatedToolOutput"
    payload = _hso(event, additionalContext=additional_context, **{key: updated_tool_output})
    return Decision("update_output", event, payload)


def block(reason: str, *, event: str = "Stop", suppress_original_prompt: bool = False) -> Decision:
    """Block with the shape the event documents.

    Events with a top-level decision get ``{"decision": "block", "reason": ...}`` and exit
    0. Events that only block through the exit code (TeammateIdle, TaskCompleted,
    Elicitation, ElicitationResult, WorktreeCreate, WorktreeRemove) get the reason on
    stderr and exit 2. Other events cannot block and raise ``DecisionError``.
    """
    _text(reason, "reason")
    if event in TOP_LEVEL_BLOCK_EVENTS:
        payload: dict[str, Any] = {"decision": "block", "reason": reason}
        if suppress_original_prompt:
            if event != "UserPromptSubmit":
                raise DecisionError("suppressOriginalPrompt only applies to UserPromptSubmit")
            payload.update(_hso(event, suppressOriginalPrompt=True))
        return Decision("block", event, payload)
    if event in EXIT_2_BLOCK_EVENTS:
        return blocking_error(reason, event=event)
    _check_event(event, TOP_LEVEL_BLOCK_EVENTS | EXIT_2_BLOCK_EVENTS, "block()")
    raise AssertionError("unreachable")


def blocking_error(reason: str, *, event: str = "PreToolUse") -> Decision:
    """Exit code 2 with the reason on stderr, for events where exit 2 blocks."""
    _check_event(event, EXIT_2_BLOCK_EVENTS, "exit code 2")
    kind = "deny" if event in PERMISSION_DECISIONS else "block"
    return Decision(kind, event, {}, 2, _text(reason, "reason"))


def add_context(text: str, *, event: str = "PostToolUse") -> Decision:
    """hookSpecificOutput.additionalContext for the events that accept it."""
    _check_event(event, CONTEXT_EVENTS, "additionalContext")
    return Decision("context", event, _hso(event, additionalContext=_text(text, "text")))


def stop(stop_reason: str, *, event: str | None = None) -> Decision:
    """``{"continue": false, "stopReason": ...}``: Claude stops processing entirely."""
    if event is not None:
        _check_event(event, set(EVENTS) - CONTINUE_IGNORED, "continue: false")
    payload = {"continue": False, "stopReason": _text(stop_reason, "stop_reason")}
    return Decision("stop", event, payload)


def system_message(text: str, *, event: str | None = None) -> Decision:
    """``{"systemMessage": ...}``: a warning shown to the user."""
    if event is not None:
        _check_event(event, set(EVENTS) - SYSTEM_MESSAGE_IGNORED, "systemMessage")
    return Decision("message", event, {"systemMessage": _text(text, "text")})


def permission_request(
    behavior: str,
    *,
    updated_input: dict[str, Any] | None = None,
    updated_permissions: Sequence[dict[str, Any]] | None = None,
    message: str | None = None,
    interrupt: bool | None = None,
) -> Decision:
    """hookSpecificOutput.decision for PermissionRequest: allow or deny on the user's behalf."""
    if behavior not in ("allow", "deny"):
        raise DecisionError('PermissionRequest behavior must be "allow" or "deny"')
    if behavior == "deny" and (updated_input is not None or updated_permissions is not None):
        raise DecisionError('updatedInput and updatedPermissions apply to "allow" only')
    if behavior == "allow" and (message is not None or interrupt is not None):
        raise DecisionError('message and interrupt apply to "deny" only')
    inner: dict[str, Any] = {"behavior": behavior}
    if updated_input is not None:
        inner["updatedInput"] = updated_input
    if updated_permissions is not None:
        inner["updatedPermissions"] = list(updated_permissions)
    if message is not None:
        inner["message"] = message
    if interrupt is not None:
        inner["interrupt"] = interrupt
    return Decision(behavior, "PermissionRequest", _hso("PermissionRequest", decision=inner))


def retry() -> Decision:
    """hookSpecificOutput.retry for PermissionDenied: tell the model it may retry."""
    return Decision("retry", "PermissionDenied", _hso("PermissionDenied", retry=True))


def display(text: str) -> Decision:
    """hookSpecificOutput.displayContent for MessageDisplay (display only)."""
    return Decision("display", "MessageDisplay", _hso("MessageDisplay", displayContent=text))


def elicitation(
    action: str, content: dict[str, Any] | None = None, *, event: str = "Elicitation"
) -> Decision:
    """hookSpecificOutput.action and content for Elicitation and ElicitationResult."""
    _check_event(event, ELICITATION_EVENTS, "elicitation()")
    if action not in ELICITATION_ACTIONS:
        raise DecisionError("action must be accept, decline or cancel")
    if content is not None and action != "accept":
        raise DecisionError('content is only used when action is "accept"')
    return Decision(action, event, _hso(event, action=action, content=content))


def watch_paths(paths: Sequence[str], *, event: str = "CwdChanged") -> Decision:
    """hookSpecificOutput.watchPaths for SessionStart, CwdChanged and FileChanged."""
    _check_event(event, WATCH_PATH_EVENTS, "watchPaths")
    return Decision("watch", event, _hso(event, watchPaths=list(paths)))


def session_start(
    additional_context: str | None = None,
    *,
    session_title: str | None = None,
    initial_user_message: str | None = None,
    watch_paths: Sequence[str] | None = None,
    reload_skills: bool | None = None,
) -> Decision:
    """The SessionStart output object with any of its five documented fields."""
    payload = _hso(
        "SessionStart",
        additionalContext=additional_context,
        sessionTitle=session_title,
        initialUserMessage=initial_user_message,
        watchPaths=list(watch_paths) if watch_paths is not None else None,
        reloadSkills=reload_skills,
    )
    if len(payload["hookSpecificOutput"]) == 1:
        raise DecisionError("session_start() needs at least one field")
    kind = "context" if additional_context is not None else "session_start"
    return Decision(kind, "SessionStart", payload)


def no_decision() -> Decision:
    """Print nothing and exit 0: the normal flow applies."""
    return Decision("none")


# --------------------------------------------------------------------------- observing


@dataclass(frozen=True)
class Observed:
    """What Claude Code would make of one hook's stdout, stderr and exit code."""

    kind: str
    reason: str | None = None
    context: tuple[str, ...] = ()
    payload: dict[str, Any] | None = None
    exit_code: int | None = None
    note: str | None = None


def _blocking_reason(event: str, payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    hso = payload.get("hookSpecificOutput")
    if isinstance(hso, dict) and hso.get("permissionDecision") == "deny":
        value = hso.get("permissionDecisionReason")
        return value if isinstance(value, str) else None
    if payload.get("decision") == "block" and isinstance(payload.get("reason"), str):
        return payload["reason"]
    return None


def observe(event: str, stdout: str, exit_code: int, stderr: str = "") -> Observed:
    """Apply the documented exit-code and stdout rules to one hook run."""
    text = stdout.strip()
    payload: dict[str, Any] | None = None
    parse_error: str | None = None
    if text.startswith("{") and text.endswith("}"):
        try:
            decoded = json.loads(text)
        except ValueError as exc:
            parse_error = str(exc)
        else:
            if isinstance(decoded, dict):
                payload = decoded
            else:
                parse_error = "top-level JSON is not an object"
    spec = EVENTS.get(event)
    exit_2_text = spec.exit_2 if spec else "unknown event"

    if exit_code == 2:
        if event in EXIT_2_BLOCK_EVENTS:
            kind = "deny" if event in PERMISSION_DECISIONS else "block"
            reason = _blocking_reason(event, payload) or stderr.strip() or None
            return Observed(kind, reason, (), payload, 2, exit_2_text)
        note = f"exit 2 does not block on {event}: {exit_2_text}"
        if event in ELICITATION_EVENTS:
            payload = None
    else:
        note = None

    context: list[str] = []
    kind = "none"
    reason: str | None = None
    if payload is not None:
        hso = payload.get("hookSpecificOutput")
        if isinstance(hso, dict):
            if isinstance(hso.get("additionalContext"), str):
                context.append(hso["additionalContext"])
            permission = hso.get("permissionDecision")
            inner = hso.get("decision")
            if permission in ("allow", "deny", "ask", "defer"):
                kind = permission
                value = hso.get("permissionDecisionReason")
                reason = value if isinstance(value, str) else None
            elif isinstance(inner, dict) and inner.get("behavior") in ("allow", "deny"):
                kind = inner["behavior"]
                value = inner.get("message")
                reason = value if isinstance(value, str) else None
            elif hso.get("retry") is True:
                kind = "retry"
            elif "displayContent" in hso:
                kind = "display"
            elif hso.get("action") in ELICITATION_ACTIONS:
                kind = hso["action"]
            elif "updatedToolOutput" in hso or "updatedMCPToolOutput" in hso:
                kind = "update_output"
        if payload.get("decision") == "block":
            kind = "block"
            value = payload.get("reason")
            reason = value if isinstance(value, str) else None
        if payload.get("continue") is False:
            kind = "stop"
            value = payload.get("stopReason")
            reason = value if isinstance(value, str) else None
        if kind == "none" and context:
            kind = "context"
        if exit_code not in (0, 2) and note is None:
            note = f"exit {exit_code} ignored because stdout holds a valid JSON object"
    elif parse_error is not None:
        kind = "error"
        note = f"stdout looks like JSON but does not parse ({parse_error}); non-blocking error"
    elif exit_code not in (0, 2):
        kind = "error"
        first = stderr.strip().splitlines()[0] if stderr.strip() else ""
        note = f"exit {exit_code} with no JSON decision: non-blocking error, the action proceeds"
        if first:
            note += f" (stderr: {first})"
    elif text and event in PLAIN_TEXT_CONTEXT_EVENTS:
        kind = "context"
        context.append(text)
    return Observed(kind, reason, tuple(context), payload, exit_code, note)


# Lower rank wins. "deny", "block", "decline" and "cancel" all stop the action; "stop"
# (continue: false) takes precedence over every event-specific field per the docs.
_RANK = {
    "stop": 0,
    "deny": 1,
    "block": 1,
    "decline": 1,
    "cancel": 1,
    "defer": 2,
    "ask": 3,
    "allow": 4,
    "accept": 4,
    "update_output": 5,
    "retry": 5,
    "display": 5,
    "context": 6,
    "timeout": 7,
    "error": 8,
    "none": 9,
}


def combine(observed: Sequence[Observed], event: str) -> Observed:
    """The merged outcome when several hooks ran for one event.

    For PreToolUse the docs give the precedence deny > defer > ask > allow; the same
    ordering is applied to every event, additionalContext is kept from every hook, and
    the reason comes from the winning hook.
    """
    if not observed:
        return Observed("none", note="no handler matched")
    winner = min(observed, key=lambda item: _RANK.get(item.kind, 9))
    context = tuple(text for item in observed for text in item.context)
    notes = [item.note for item in observed if item.note]
    return Observed(
        winner.kind,
        winner.reason,
        context,
        winner.payload,
        winner.exit_code,
        "; ".join(notes) if notes else None,
    )
