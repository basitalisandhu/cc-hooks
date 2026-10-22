import io
import json

import pytest

from cc_hooks.cli import main
from cc_hooks.events import EVENTS
from cc_hooks.output import OUTPUT_FIELDS, stdout_warnings, validate_output

from .conftest import event, write_json
from .test_decide import ROUND_TRIPS


@pytest.mark.parametrize("_name,build,event_name,_kind", ROUND_TRIPS)
def test_builder_outputs_are_valid(_name, build, event_name, _kind):
    assert validate_output(event_name, build().to_dict()) == []


def test_registry_covers_every_event_and_each_documented_field():
    assert set(OUTPUT_FIELDS) == set(EVENTS)
    for name, spec in EVENTS.items():
        for field in spec.output_fields:
            path = (
                field
                if field in {"decision", "reason", "continue", "stopReason"}
                else "hookSpecificOutput." + field
            )
            assert path in OUTPUT_FIELDS[name], (name, path)


def test_replay_validates_json_on_nonzero_exit_without_reclassifying_plain_text():
    assert stdout_warnings("PreToolUse", '{"permissionDecision":"deny"}', 2)
    assert stdout_warnings("PreToolUse", "plain stderr-style message", 0) == []
    assert stdout_warnings("PreToolUse", "{", 2) == []
    assert stdout_warnings("PreToolUse", "{", 0)


@pytest.mark.parametrize("value", [[], None, True, 1, "deny"])
def test_json_without_an_object_is_not_a_hook_response(value):
    assert "object" in " ".join(validate_output("PreToolUse", value))


def test_wrong_event_enum_types_and_placement_have_distinct_diagnostics():
    warnings = validate_output(
        "PreToolUse",
        {
            "permissionDecision": "deny",
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "permissionDecision": "approve",
                "updatedInput": [],
            },
            "continue": 0,
        },
    )
    assert any(
        "permissionDecision" in warning and "undocumented" in warning for warning in warnings
    )
    assert any("hookEventName" in warning and "PreToolUse" in warning for warning in warnings)
    assert any("approve" in warning for warning in warnings)
    assert any("updatedInput" in warning and "object" in warning for warning in warnings)
    assert any("continue" in warning and "boolean" in warning for warning in warnings)


def test_pretool_decision_on_posttool_and_missing_event_name():
    warnings = validate_output(
        "PostToolUse", {"hookSpecificOutput": {"permissionDecision": "deny"}}
    )
    assert any(
        "permissionDecision" in warning and "undocumented" in warning for warning in warnings
    )
    assert any("hookEventName" in warning for warning in warnings)


def test_permission_request_nested_fields_and_behavior_restrictions():
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {
                "behavior": "deny",
                "updatedInput": {},
                "interrupt": "yes",
                "updatedPermissions": [1],
                "mystery": True,
            },
        }
    }
    warnings = validate_output("PermissionRequest", output)
    assert any("updatedInput" in warning and "allow" in warning for warning in warnings)
    assert any("interrupt" in warning and "boolean" in warning for warning in warnings)
    assert any("updatedPermissions" in warning and "objects" in warning for warning in warnings)
    assert any("mystery" in warning for warning in warnings)
    assert (
        validate_output(
            "PermissionRequest",
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": "allow", "updatedPermissions": [{}]},
                }
            },
        )
        == []
    )


def test_ignored_fields_and_array_item_types():
    assert any("ignored" in w for w in validate_output("Notification", {"continue": False}))
    assert any("no effect" in w for w in validate_output("PreToolUse", {"suppressOutput": True}))
    assert any(
        "strings" in w
        for w in validate_output(
            "CwdChanged",
            {
                "hookSpecificOutput": {"hookEventName": "CwdChanged", "watchPaths": [3]},
            },
        )
    )
    assert any("reason" in w for w in validate_output("Stop", {"decision": "block"}))


def test_validate_output_cli_file_stdin_and_bad_json(tmp_path, capsys, monkeypatch):
    path = tmp_path / "response.json"
    write_json(
        path, {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"}}
    )
    assert main(["validate-output", "PreToolUse", str(path)]) == 0
    assert "valid" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO('{"permissionDecision":"deny"}'))
    assert main(["validate-output", "PreToolUse", "-", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["warnings"]
    path.write_text("{", encoding="utf-8")
    assert main(["validate-output", "PreToolUse", str(path)]) == 2


def test_replay_reports_advisory_output_warnings_without_changing_verdict(tmp_path, capsys):
    fixture = tmp_path / "fixture.json"
    write_json(fixture, {"event": event(), "expect": {"decision": "none"}})
    settings = tmp_path / "settings.json"
    write_json(
        settings,
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "printf '%s' '{\"permissionDecision\":\"deny\"}'",
                            }
                        ],
                    }
                ]
            }
        },
    )
    report = tmp_path / "report.json"
    assert (
        main(
            [
                "test",
                str(fixture),
                "--settings",
                str(settings),
                "--cwd",
                str(tmp_path),
                "--report",
                str(report),
            ]
        )
        == 0
    )
    assert "output warning:" in capsys.readouterr().out
    assert json.loads(report.read_text(encoding="utf-8"))[0]["output_warnings"]
