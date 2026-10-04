"""Builders print the documented shapes, refuse undocumented ones, and observe() reads them back."""

from __future__ import annotations

import io
import json

import pytest

from cc_hooks import (
    EVENTS,
    Decision,
    DecisionError,
    add_context,
    allow,
    ask,
    block,
    blocking_error,
    defer,
    deny,
    display,
    elicitation,
    no_decision,
    permission_request,
    retry,
    session_start,
    stop,
    system_message,
    update_input,
    update_output,
    watch_paths,
)
from cc_hooks.decide import (
    CONTEXT_EVENTS,
    EXIT_2_BLOCK_EVENTS,
    TOP_LEVEL_BLOCK_EVENTS,
    Observed,
    combine,
    observe,
)

ROUND_TRIPS = [
    ("allow", lambda: allow("fine"), "PreToolUse", "allow"),
    ("allow-input", lambda: allow(updated_input={"command": "ls"}), "PreToolUse", "allow"),
    ("deny", lambda: deny("no"), "PreToolUse", "deny"),
    ("ask", lambda: ask("sure?"), "PreToolUse", "ask"),
    ("defer", lambda: defer(), "PreToolUse", "defer"),
    ("update-input", lambda: update_input({"command": "ls"}, decision="ask"), "PreToolUse", "ask"),
    ("deny-model", lambda: deny("not here", event="PreModelSwitch"), "PreModelSwitch", "deny"),
    ("update-output", lambda: update_output({"stdout": "x"}), "PostToolUse", "update_output"),
    ("block-stop", lambda: block("keep going", event="Stop"), "Stop", "block"),
    (
        "block-prompt",
        lambda: block("nope", event="UserPromptSubmit", suppress_original_prompt=True),
        "UserPromptSubmit",
        "block",
    ),
    ("context", lambda: add_context("branch main", event="PostToolUse"), "PostToolUse", "context"),
    ("stop", lambda: stop("build failed", event="PostToolBatch"), "PostToolBatch", "stop"),
    ("message", lambda: system_message("heads up", event="PreToolUse"), "PreToolUse", "none"),
    (
        "permission-allow",
        lambda: permission_request("allow", updated_input={"command": "npm run lint"}),
        "PermissionRequest",
        "allow",
    ),
    (
        "permission-deny",
        lambda: permission_request("deny", message="no", interrupt=True),
        "PermissionRequest",
        "deny",
    ),
    ("retry", retry, "PermissionDenied", "retry"),
    ("display", lambda: display("plain"), "MessageDisplay", "display"),
    (
        "elicit-accept",
        lambda: elicitation("accept", {"username": "alice"}),
        "Elicitation",
        "accept",
    ),
    (
        "elicit-result",
        lambda: elicitation("decline", event="ElicitationResult"),
        "ElicitationResult",
        "decline",
    ),
    ("watch", lambda: watch_paths(["/a/.env"], event="CwdChanged"), "CwdChanged", "none"),
    (
        "session-start",
        lambda: session_start("facts", session_title="t", reload_skills=True),
        "SessionStart",
        "context",
    ),
]


@pytest.mark.parametrize(
    "label, build, event_name, observed_kind", ROUND_TRIPS, ids=[r[0] for r in ROUND_TRIPS]
)
def test_builder_round_trips_through_observe(label, build, event_name, observed_kind):
    decision = build()
    assert decision.exit_code == 0
    text = decision.to_json()
    payload = json.loads(text)
    assert payload == decision.to_dict()
    hso = payload.get("hookSpecificOutput")
    if hso is not None:
        assert hso["hookEventName"] == event_name
        assert set(hso) - {"hookEventName"} <= {
            f.split(".")[0] for f in EVENTS[event_name].output_fields
        } | {"suppressOriginalPrompt"}
    seen = observe(event_name, text, 0)
    assert seen.kind == observed_kind
    assert seen.payload == payload


def test_exact_documented_shapes():
    assert deny("Use rg instead").to_dict() == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Use rg instead",
        }
    }
    assert block("Test suite must pass", event="Stop").to_dict() == {
        "decision": "block",
        "reason": "Test suite must pass",
    }
    assert stop("Build failed").to_dict() == {"continue": False, "stopReason": "Build failed"}
    assert retry().to_dict() == {
        "hookSpecificOutput": {"hookEventName": "PermissionDenied", "retry": True}
    }
    assert permission_request("allow", updated_input={"command": "npm run lint"}).to_dict() == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow", "updatedInput": {"command": "npm run lint"}},
        }
    }
    assert update_output({"stdout": "[redacted]"}, mcp=True).to_dict()["hookSpecificOutput"] == {
        "hookEventName": "PostToolUse",
        "updatedMCPToolOutput": {"stdout": "[redacted]"},
    }


@pytest.mark.parametrize(
    "call, message",
    [
        (lambda: update_output({}, event="PreToolUse"), "not documented for PreToolUse"),
        (lambda: defer(event="PreModelSwitch"), "not documented for PreModelSwitch"),
        (lambda: deny("x", event="PostToolUse"), "not documented for PostToolUse"),
        (lambda: allow(updated_input={"a": 1}, event="PreModelSwitch"), "neither updatedInput"),
        (lambda: add_context("x", event="Notification"), "additionalContext is not documented"),
        (lambda: stop("x", event="SessionEnd"), "continue: false is not documented"),
        (lambda: system_message("x", event="Setup"), "systemMessage is not documented"),
        (lambda: block("x", event="Notification"), "not documented for Notification"),
        (lambda: blocking_error("x", event="PostToolUse"), "exit code 2 is not documented"),
        (lambda: elicitation("decline", {"a": 1}), 'only used when action is "accept"'),
        (lambda: elicitation("maybe"), "accept, decline or cancel"),
        (lambda: permission_request("deny", updated_input={"a": 1}), '"allow" only'),
        (lambda: permission_request("allow", message="x"), '"deny" only'),
        (lambda: permission_request("maybe"), "allow"),
        (lambda: update_input({"a": 1}, decision="defer"), "ignored for"),
        (lambda: update_input("notadict"), "must be a dict"),
        (lambda: watch_paths(["/x"], event="Stop"), "watchPaths is not documented"),
        (
            lambda: block("x", event="UserPromptExpansion", suppress_original_prompt=True),
            "only applies",
        ),
        (lambda: deny(""), "non-empty"),
        (lambda: deny("x", event="Nope"), "unknown hook event"),
        (lambda: session_start(), "at least one field"),
    ],
)
def test_undocumented_combinations_are_refused(call, message):
    with pytest.raises(DecisionError, match=message):
        call()


def test_exit_codes_and_emit():
    out, err = io.StringIO(), io.StringIO()
    assert deny("no").emit(out, err) == 0
    assert json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert err.getvalue() == ""

    out, err = io.StringIO(), io.StringIO()
    blocked = blocking_error("stop that", event="PreToolUse")
    assert blocked.exit_code == 2 and blocked.kind == "deny" and blocked.reason == "stop that"
    assert blocked.emit(out, err) == 2
    assert out.getvalue() == "" and err.getvalue() == "stop that\n"

    assert block("idle check", event="TeammateIdle").exit_code == 2
    assert block("not done", event="TaskCompleted").kind == "block"
    assert no_decision().emit(io.StringIO(), io.StringIO()) == 0
    with pytest.raises(SystemExit) as info:
        deny("bye").exit()
    assert info.value.code == 0


def test_with_fields_adds_universal_keys():
    decision = allow().with_fields(system_message="careful", terminal_sequence="\x07")
    assert decision.to_dict()["systemMessage"] == "careful"
    assert decision.to_dict()["terminalSequence"] == "\x07"
    with pytest.raises(DecisionError):
        elicitation("accept").with_fields(system_message="x")
    with pytest.raises(DecisionError):
        display("x").with_fields(continue_=False)


def test_reason_property_finds_the_reason_in_every_shape():
    assert deny("a").reason == "a"
    assert block("b", event="Stop").reason == "b"
    assert stop("c").reason == "c"
    assert permission_request("deny", message="d").reason == "d"
    assert blocking_error("e").reason == "e"
    assert allow().reason is None


def test_event_sets_agree_with_the_registry():
    assert set(EVENTS) >= TOP_LEVEL_BLOCK_EVENTS
    assert {name for name, spec in EVENTS.items() if spec.can_block} == EXIT_2_BLOCK_EVENTS
    assert {
        name for name, spec in EVENTS.items() if "additionalContext" in spec.output_fields
    } == CONTEXT_EVENTS


# --------------------------------------------------------------------------- observe


def test_observe_exit_2_blocks_with_stderr_reason():
    seen = observe("PreToolUse", "", 2, "rm is not allowed\n")
    assert seen.kind == "deny" and seen.reason == "rm is not allowed"
    seen = observe("Stop", "", 2, "keep going")
    assert seen.kind == "block"
    seen = observe("PreToolUse", json.dumps(deny("json wins").to_dict()), 2, "stderr text")
    assert seen.reason == "json wins"


def test_observe_exit_2_does_not_block_where_the_docs_say_so():
    seen = observe("PostToolUse", "", 2, "warning")
    assert seen.kind == "none" and "does not block" in (seen.note or "")
    seen = observe("Elicitation", json.dumps(elicitation("accept").to_dict()), 2)
    assert seen.kind == "block"  # exit 2 denies; hookSpecificOutput is ignored on exit 2


def test_observe_other_exit_codes():
    seen = observe("PreToolUse", "", 1, "boom")
    assert seen.kind == "error" and "non-blocking" in (seen.note or "")
    seen = observe("PreToolUse", json.dumps(ask("hm").to_dict()), 1)
    assert seen.kind == "ask" and "ignored" in (seen.note or "")


def test_observe_plain_text_and_parse_failures():
    assert observe("SessionStart", "Current branch: main", 0).kind == "context"
    assert observe("PreToolUse", "Current branch: main", 0).kind == "none"
    assert observe("PreToolUse", "{not json}", 0).kind == "error"
    assert observe("PreToolUse", "[1, 2]", 0).kind == "none"
    assert observe("PreToolUse", "", 0).kind == "none"


def test_observe_reads_every_decision_shape():
    assert observe("Stop", '{"decision": "block", "reason": "r"}', 0).reason == "r"
    assert observe("PostToolBatch", '{"continue": false, "stopReason": "s"}', 0).kind == "stop"
    assert (
        observe(
            "PermissionRequest", json.dumps(permission_request("deny", message="m").to_dict()), 0
        ).reason
        == "m"
    )
    seen = observe("PostToolUse", json.dumps(add_context("c").to_dict()), 0)
    assert seen.kind == "context" and seen.context == ("c",)


def test_combine_applies_documented_precedence():
    observed = [
        Observed("allow", context=("a",)),
        Observed("ask", reason="ask reason", context=("b",)),
        Observed("defer"),
        Observed("deny", reason="deny reason"),
    ]
    merged = combine(observed, "PreToolUse")
    assert merged.kind == "deny" and merged.reason == "deny reason"
    assert merged.context == ("a", "b")
    assert combine(observed[:3], "PreToolUse").kind == "defer"
    assert combine(observed[:2], "PreToolUse").kind == "ask"
    assert combine([observed[0]], "PreToolUse").kind == "allow"
    assert combine([], "PreToolUse").kind == "none"
    assert combine([Observed("block"), Observed("stop", reason="s")], "Stop").kind == "stop"


def test_decision_is_a_plain_frozen_record():
    decision = Decision("none")
    assert decision.to_dict() == {} and decision.to_json() == "{}"
    with pytest.raises(AttributeError):
        decision.kind = "deny"  # type: ignore[misc]
