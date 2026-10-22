"""Advisory JSON-output checks; not a Claude Code runtime emulator.

Field scope follows events.py and https://code.claude.com/docs/en/hooks.
Replacement tool-output schemas are intentionally outside this validator.
Unknown extension fields are warnings, not proof the response will fail.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .decide import CONTINUE_IGNORED, PERMISSION_DECISIONS, SYSTEM_MESSAGE_IGNORED
from .events import EVENTS


@dataclass(frozen=True)
class OutputField:
    kind: str
    choices: tuple[str, ...] = ()


_STRING = OutputField("string")
_BOOL = OutputField("boolean")
_OBJECT = OutputField("object")
_ANY = OutputField("tool-specific")
_TOP = {
    "continue": _BOOL,
    "stopReason": _STRING,
    "suppressOutput": _BOOL,
    "systemMessage": _STRING,
    "terminalSequence": _STRING,
}
_TYPES = {
    "decision": OutputField("string", ("block",)),
    "reason": _STRING,
    "additionalContext": _STRING,
    "permissionDecision": OutputField("string", ("allow", "deny", "ask")),
    "permissionDecisionReason": _STRING,
    "updatedInput": _OBJECT,
    "updatedToolOutput": _ANY,
    "updatedMCPToolOutput": _ANY,
    "classifierContext": _STRING,
    "suppressOriginalPrompt": _BOOL,
    "retry": _BOOL,
    "displayContent": _STRING,
    "watchPaths": OutputField("strings"),
    "action": OutputField("string", ("accept", "decline", "cancel")),
    "content": _OBJECT,
    "worktreePath": _STRING,
    "initialUserMessage": _STRING,
    "sessionTitle": _STRING,
    "reloadSkills": _BOOL,
    "decision.behavior": OutputField("string", ("allow", "deny")),
    "decision.updatedInput": _OBJECT,
    "decision.updatedPermissions": OutputField("objects"),
    "decision.message": _STRING,
    "decision.interrupt": _BOOL,
}


def _fields(event: str) -> dict[str, OutputField]:
    fields = dict(_TOP)
    for name in EVENTS[event].output_fields:
        path = (
            name
            if name in {"decision", "reason", "continue", "stopReason"}
            else "hookSpecificOutput." + name
        )
        fields[path] = _TOP[name] if name in _TOP else _TYPES[name]
    if any(path.startswith("hookSpecificOutput.") for path in fields):
        fields["hookSpecificOutput"] = _OBJECT
        fields["hookSpecificOutput.hookEventName"] = _STRING
    if event == "PermissionRequest":
        fields["hookSpecificOutput.decision"] = _OBJECT
    if event in PERMISSION_DECISIONS:
        fields["hookSpecificOutput.permissionDecision"] = OutputField(
            "string", PERMISSION_DECISIONS[event]
        )
    return fields


OUTPUT_FIELDS = {event: _fields(event) for event in EVENTS}


def _matches(value: Any, field: OutputField) -> bool:
    if field.kind == "tool-specific":
        return True
    if field.kind == "string":
        return isinstance(value, str)
    if field.kind == "boolean":
        return isinstance(value, bool)
    if field.kind == "object":
        return isinstance(value, dict)
    item_type = str if field.kind == "strings" else dict
    return isinstance(value, list) and all(isinstance(item, item_type) for item in value)


def validate_output(event: str, value: Any) -> list[str]:
    """Return warnings without mutating the response or changing its verdict."""
    if event not in OUTPUT_FIELDS:
        raise ValueError(f"unknown hook event {event!r}")
    if not isinstance(value, dict):
        return ["$: hook output must be a JSON object"]
    fields = OUTPUT_FIELDS[event]
    warnings: list[str] = []

    def walk(obj: dict[str, Any], prefix: str = "") -> None:
        for key, item in obj.items():
            path = f"{prefix}.{key}" if prefix else key
            field = fields.get(path)
            if field is None:
                warnings.append(f"$.{path}: undocumented for {event}; it may be ignored")
                continue
            if not _matches(item, field):
                expected = (
                    f"array of {field.kind}" if field.kind in {"strings", "objects"} else field.kind
                )
                warnings.append(f"$.{path}: expected {expected}")
                continue
            if field.choices and item not in field.choices:
                warnings.append(f"$.{path}: {item!r} is not one of {', '.join(field.choices)}")
            if path in {"hookSpecificOutput", "hookSpecificOutput.decision"} and isinstance(
                item, dict
            ):
                walk(item, path)

    walk(value)
    hso = value.get("hookSpecificOutput")
    if isinstance(hso, dict) and "hookSpecificOutput" in fields:
        if hso.get("hookEventName") != event:
            warnings.append(f"$.hookSpecificOutput.hookEventName: expected {event!r}")
        if (
            event == "PreToolUse"
            and "updatedInput" in hso
            and hso.get("permissionDecision") not in ("allow", "ask")
        ):
            warnings.append(
                "$.hookSpecificOutput.updatedInput: only effective with "
                "permissionDecision allow or ask"
            )
        if event == "PermissionRequest" and isinstance(hso.get("decision"), dict):
            decision = hso["decision"]
            behavior = decision.get("behavior")
            if behavior not in ("allow", "deny"):
                warnings.append("$.hookSpecificOutput.decision.behavior: expected allow or deny")
            for key in ("updatedInput", "updatedPermissions"):
                if key in decision and behavior != "allow":
                    warnings.append(
                        f"$.hookSpecificOutput.decision.{key}: only effective with behavior allow"
                    )
            for key in ("message", "interrupt"):
                if key in decision and behavior != "deny":
                    warnings.append(
                        f"$.hookSpecificOutput.decision.{key}: only effective with behavior deny"
                    )
    if "continue" in value and event in CONTINUE_IGNORED:
        warnings.append(f"$.continue: ignored for {event}")
    if "systemMessage" in value and event in SYSTEM_MESSAGE_IGNORED:
        warnings.append(f"$.systemMessage: ignored for {event}")
    if "stopReason" in value and value.get("continue") is not False:
        warnings.append("$.stopReason: only effective with continue false")
    if "suppressOutput" in value:
        warnings.append("$.suppressOutput: accepted for compatibility but has no effect")
    if value.get("decision") == "block" and not isinstance(value.get("reason"), str):
        warnings.append("$.reason: a block decision requires a reason string")
    return warnings


def stdout_warnings(event: str, stdout: str, exit_code: int) -> list[str]:
    """Replay accepts plain text and honours parsed JSON on any exit code."""
    text = stdout.strip()
    if not text or text[0] not in "{[":
        return []
    try:
        value = json.loads(text)
    except ValueError:
        return ["$: output looks like JSON but cannot be parsed"] if exit_code == 0 else []
    return validate_output(event, value)
