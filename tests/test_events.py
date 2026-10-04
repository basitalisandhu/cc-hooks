"""Every documented event parses its example payload; malformed input raises HookInputError."""

from __future__ import annotations

import copy
import io
import json
from dataclasses import fields as dataclass_fields

import pytest

from cc_hooks import (
    COMMON_FIELDS,
    EVENT_CLASSES,
    EVENT_NAMES,
    EVENTS,
    Event,
    FileChanged,
    HookInputError,
    PostToolUse,
    PreModelSwitch,
    PreToolUse,
    ToolEvent,
    dry_run,
    parse_event,
    read_event,
)
from cc_hooks.events import TOOL_EVENTS

from .conftest import event

DOC_ORDER = [
    "SessionStart",
    "Setup",
    "InstructionsLoaded",
    "UserPromptSubmit",
    "UserPromptExpansion",
    "MessageDisplay",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PostToolUseFailure",
    "PostToolBatch",
    "PermissionDenied",
    "Notification",
    "SubagentStart",
    "SubagentStop",
    "TaskCreated",
    "TaskCompleted",
    "Stop",
    "StopFailure",
    "TeammateIdle",
    "ConfigChange",
    "CwdChanged",
    "DirectoryAdded",
    "FileChanged",
    "WorktreeCreate",
    "WorktreeRemove",
    "PreCompact",
    "PostCompact",
    "PreModelSwitch",
    "PostModelSwitch",
    "SessionEnd",
    "Elicitation",
    "ElicitationResult",
]


def test_registry_matches_the_docs_order_and_count():
    assert list(EVENT_NAMES) == DOC_ORDER
    assert len(EVENTS) == 33
    assert set(EVENT_CLASSES) == set(EVENTS)


@pytest.mark.parametrize("name", DOC_ORDER)
def test_documented_example_parses(name: str):
    spec = EVENTS[name]
    parsed = parse_event(copy.deepcopy(spec.example))
    assert isinstance(parsed, EVENT_CLASSES[name])
    assert parsed.hook_event_name == name
    assert parsed.extra == {}
    expected = {k: v for k, v in spec.example.items() if v is not None}
    assert parsed.to_dict() == expected
    assert json.loads(parsed.to_json()) == expected
    if spec.matcher_field is not None:
        assert parsed.matcher_value is not None
    else:
        assert parsed.matcher_value is None


@pytest.mark.parametrize("name", DOC_ORDER)
def test_dataclass_fields_match_the_spec(name: str):
    """The typed class and the documented field list cannot drift apart."""
    cls = EVENT_CLASSES[name]
    declared = {f.name for f in dataclass_fields(cls)} - {"extra"}
    documented = {f.name for f in COMMON_FIELDS} | {f.name for f in EVENTS[name].fields}
    assert declared == documented


@pytest.mark.parametrize("name", DOC_ORDER)
def test_required_fields_are_required_on_the_class(name: str):
    cls = EVENT_CLASSES[name]
    import dataclasses

    no_default = {
        f.name
        for f in dataclass_fields(cls)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    documented = set(EVENTS[name].required_fields) | {"session_id"}
    assert no_default == documented


def test_unknown_event_is_rejected():
    with pytest.raises(HookInputError, match="unknown hook event 'Nope'"):
        parse_event(event("Nope"))


def test_non_object_and_missing_name_are_rejected():
    with pytest.raises(HookInputError, match="JSON object"):
        parse_event([1, 2])
    with pytest.raises(HookInputError, match="no hook_event_name"):
        parse_event({"session_id": "x"})


def test_missing_required_field_is_rejected_in_strict_mode():
    payload = event("PreToolUse")
    del payload["tool_input"]
    with pytest.raises(HookInputError, match="missing required field 'tool_input'"):
        parse_event(payload)


def test_wrong_type_is_rejected_in_strict_mode():
    payload = event("PreToolUse", tool_input="ls")
    with pytest.raises(HookInputError, match="should be object"):
        parse_event(payload)


def test_lenient_mode_only_needs_name_and_session():
    payload = {"session_id": "s", "hook_event_name": "Stop", "future_field": 1}
    parsed = parse_event(payload, strict=False)
    assert parsed.extra == {"future_field": 1}
    with pytest.raises(HookInputError):
        parse_event({"hook_event_name": "Stop"}, strict=False)


def test_unknown_fields_land_in_extra_and_round_trip():
    payload = event("PreToolUse", new_thing={"a": 1})
    parsed = parse_event(payload)
    assert parsed.extra == {"new_thing": {"a": 1}}
    assert parsed.to_dict() == payload


def test_read_event_reads_a_stream():
    parsed = read_event(io.StringIO(json.dumps(event("Stop"))))
    assert parsed.hook_event_name == "Stop"


def test_read_event_rejects_empty_and_invalid_json():
    with pytest.raises(HookInputError, match="empty"):
        read_event(io.StringIO("   "))
    with pytest.raises(HookInputError, match="not valid JSON"):
        read_event(io.StringIO("{not json"))


def test_tool_event_helpers():
    parsed = parse_event(event("PreToolUse", tool_input={"command": "cat .env"}))
    assert isinstance(parsed, PreToolUse)
    assert isinstance(parsed, ToolEvent)
    assert parsed.command == "cat .env"
    assert parsed.file_path is None
    assert not parsed.is_mcp_tool
    mcp = parse_event(
        event("PreToolUse", tool_name="mcp__memory__create", tool_input={"file_path": "/x"})
    )
    assert mcp.is_mcp_tool and mcp.file_path == "/x" and mcp.command is None
    post = parse_event(event("PostToolUse"))
    assert isinstance(post, PostToolUse)
    assert post.duration_ms is None


def test_tool_events_share_matcher_field():
    for name in TOOL_EVENTS:
        assert EVENTS[name].matcher_field == "tool_name"


def test_matcher_value_special_cases():
    changed = parse_event(event("FileChanged", file_path="/home/user/demo/.envrc", event="change"))
    assert isinstance(changed, FileChanged)
    assert changed.matcher_value == ".envrc"
    switch = parse_event(
        event("PreModelSwitch", from_model="<a>", to_model="<b>[1m]", requested_model=None)
    )
    assert isinstance(switch, PreModelSwitch)
    assert switch.matcher_value == "<b>"
    stop = parse_event(event("Stop"))
    assert stop.matcher_value is None and stop.spec.matcher_field is None


def test_in_subagent_flag():
    assert not parse_event(event("Stop")).in_subagent
    assert parse_event(event("Stop", agent_id="agent-1", agent_type="Explore")).in_subagent


def test_events_are_frozen():
    parsed = parse_event(event("Stop"))
    with pytest.raises(AttributeError):
        parsed.cwd = "/elsewhere"  # type: ignore[misc]


def test_dry_run_reads_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CC_HOOKS_DRY_RUN", raising=False)
    assert not dry_run()
    monkeypatch.setenv("CC_HOOKS_DRY_RUN", "0")
    assert not dry_run()
    monkeypatch.setenv("CC_HOOKS_DRY_RUN", "1")
    assert dry_run()


def test_spec_url_points_at_the_event_section():
    assert EVENTS["PreToolUse"].url == "https://code.claude.com/docs/en/hooks#pretooluse"
    assert all(isinstance(spec.example, dict) for spec in EVENTS.values())
    assert all(issubclass(cls, Event) for cls in EVENT_CLASSES.values())
