"""The cc-hooks command line: events, explain, test and record."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_hooks.cli import main, render_events_markdown

from .conftest import Tree, event, write_json


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_events_table_lists_all_events(capsys):
    code, out = run(capsys, "events")
    assert code == 0
    assert "33 hook events" in out
    assert "PreToolUse" in out and "ElicitationResult" in out
    code, out = run(capsys, "events", "--json")
    assert code == 0 and len(json.loads(out)["events"]) == 33
    code, out = run(capsys, "events", "--markdown")
    assert code == 0 and out == render_events_markdown()


def test_version(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert "cc-hooks 0.1.2" in capsys.readouterr().out


def write_event(path: Path, **fields) -> Path:
    write_json(path, event(**fields))
    return path


def test_explain_lists_handlers_in_order(tree: Tree, tmp_path: Path, capsys):
    payload = write_event(tmp_path / "e.json", tool_input={"command": "git push"})
    code, out = run(
        capsys, "explain", str(payload), "--cwd", str(tree.project), "--home", str(tree.home)
    )
    assert code == 0, out
    assert "PreToolUse  (tool_name='Bash')" in out
    assert "handlers that would run: 4 of 6 PreToolUse handler(s)" in out
    lines = [line for line in out.splitlines() if line.strip().startswith(("1.", "2.", "3.", "4."))]
    assert "[user]" in lines[0] and "[user]" in lines[1]
    assert "[project]" in lines[2] and "if 'Bash(git *)'" in lines[2]
    assert "[plugin:demo@local]" in lines[3]
    assert "not matched:" in out and "'Edit|Write'" in out and "'mcp__memory__.*'" in out
    assert "exit code 2: blocks. Blocks the tool call" in out
    assert "permissionDecision" in out
    assert "docs: https://code.claude.com/docs/en/hooks#pretooluse" in out
    assert "plugins/installed_plugins.json" not in out
    assert "warning: plugin ghost@local" in out


def test_explain_json_and_non_command_handlers(tree: Tree, tmp_path: Path, capsys):
    payload = write_event(tmp_path / "s.json", name="SessionStart", source="startup")
    code, out = run(
        capsys,
        "explain",
        str(payload),
        "--cwd",
        str(tree.project),
        "--home",
        str(tree.home),
        "--json",
    )
    assert code == 0, out
    data = json.loads(out)
    assert data["event"] == "SessionStart" and data["matcher_value"] == "startup"
    assert [h["type"] for h in data["handlers"]] == ["prompt"]
    assert data["can_block"] is False
    assert any(s["kind"] == "plugin:demo@local" for s in data["sources"])


def test_explain_reads_stdin_and_rejects_bad_input(tmp_path: Path, capsys, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    code, out = run(capsys, "explain", str(bad))
    assert code == 2 and "not valid JSON" in out
    unknown = tmp_path / "u.json"
    write_json(unknown, {"session_id": "s", "hook_event_name": "Nope"})
    code, out = run(capsys, "explain", str(unknown), "--cwd", str(tmp_path))
    assert code == 2 and "unknown hook event" in out


def test_explain_warns_on_schema_mismatch_but_still_explains(tree: Tree, tmp_path: Path, capsys):
    payload = tmp_path / "loose.json"
    write_json(payload, {"session_id": "s", "hook_event_name": "Stop"})
    code, out = run(
        capsys, "explain", str(payload), "--cwd", str(tree.project), "--home", str(tree.home)
    )
    assert code == 0
    assert "warning: payload does not match the documented schema" in out
    assert "exit2.py" in out


def fixtures_dir(tmp_path: Path) -> Path:
    fixtures = tmp_path / "fixtures"
    write_json(
        fixtures / "01-deny.json",
        {
            "event": event(tool_input={"command": "cat .env"}),
            "expect": {"decision": "deny", "reason_contains": "SECRET", "handlers": 3},
        },
    )
    write_json(
        fixtures / "02-ask-from-plugin.json",
        {
            "event": event(tool_input={"command": "ls"}),
            "expect": {"decision": "ask", "reason_contains": "plugin asks"},
        },
    )
    write_json(
        fixtures / "03-stop-blocks.json",
        {
            "event": event("Stop"),
            "expect": {"decision": "block", "reason_contains": "exit2", "exit_code": 2},
        },
    )
    write_json(
        fixtures / "04-context.json",
        {
            "event": event("PostToolUse"),
            "expect": {"decision": "context", "context_contains": "dry_run=1"},
        },
    )
    write_json(
        fixtures / "05-failure-error.json",
        {"event": event("PostToolUseFailure"), "expect": {"decision": "error"}},
    )
    write_json(
        fixtures / "06-bare-event.json",
        event("Notification", message="m", notification_type="idle_prompt"),
    )
    return fixtures


def test_test_runner_passes_matching_fixtures(tree: Tree, tmp_path: Path, capsys):
    fixtures = fixtures_dir(tmp_path)
    code, out = run(
        capsys, "test", str(fixtures), "--cwd", str(tree.project), "--home", str(tree.home)
    )
    assert code == 0, out
    assert "6 fixture(s), dry run (CC_HOOKS_DRY_RUN=1)" in out
    assert out.count("PASS") == 6
    assert "summary: 6 passed, 0 failed" in out
    assert "decision=deny" in out and 'reason="secret file"' in out
    assert "decision=ask" in out and "plugin asks from" in out
    assert "decision=block" in out and "decision=context" in out and "decision=error" in out


def test_test_runner_reports_failures_and_exits_1(tree: Tree, tmp_path: Path, capsys):
    fixtures = tmp_path / "f"
    write_json(
        fixtures / "wrong.json",
        {
            "event": event(tool_input={"command": "cat .env"}),
            "expect": {"decision": "allow", "handlers": 1},
        },
    )
    report = tmp_path / "report.json"
    code, out = run(
        capsys,
        "test",
        str(fixtures),
        "--cwd",
        str(tree.project),
        "--home",
        str(tree.home),
        "--report",
        str(report),
    )
    assert code == 1
    assert "FAIL  wrong.json" in out
    assert "expected: decision deny, expected allow" in out
    assert "expected: 3 handler(s) matched, expected 1" in out
    assert "summary: 0 passed, 1 failed" in out
    data = json.loads(report.read_text())
    assert data[0]["status"] == "FAIL" and len(data[0]["failures"]) == 2


def test_test_runner_dry_run_versus_exec(tree: Tree, tmp_path: Path, capsys):
    write_json(
        tree.project / ".claude" / "settings.json",
        {
            "hooks": {
                "PostToolUse": [
                    {
                        "hooks": [
                            {"type": "command", "command": f'"{tree.script("side_effect.py")}"'}
                        ]
                    }
                ]
            }
        },
    )
    (tree.project / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": __import__("sys").executable,
                                    "args": [tree.script("side_effect.py")],
                                }
                            ]
                        }
                    ]
                }
            }
        )
    )
    fixtures = tmp_path / "f"
    write_json(
        fixtures / "post.json",
        {"event": event("PostToolUse"), "expect": {"decision": "none", "handlers": 1}},
    )
    marker = tree.hooks / "marker.txt"
    code, out = run(
        capsys,
        "test",
        str(fixtures),
        "--cwd",
        str(tree.project),
        "--home",
        str(tree.home),
        "--no-plugins",
    )
    assert code == 0, out
    assert not marker.exists()
    code, out = run(
        capsys,
        "test",
        str(fixtures),
        "--cwd",
        str(tree.project),
        "--home",
        str(tree.home),
        "--no-plugins",
        "--exec",
    )
    assert code == 0, out
    assert marker.read_text() == "ran"
    assert "exec" in out.splitlines()[0]


def test_test_runner_no_run_only_lists_commands(tree: Tree, tmp_path: Path, capsys):
    fixtures = fixtures_dir(tmp_path)
    code, out = run(
        capsys,
        "test",
        str(fixtures),
        "--cwd",
        str(tree.project),
        "--home",
        str(tree.home),
        "--no-run",
    )
    assert code == 0, out
    assert "would execute only" in out
    assert "would execute:" in out
    assert "DRY" in out and "PASS  01-deny.json" in out  # handlers count still checked


def test_test_runner_with_explicit_settings_file(tree: Tree, tmp_path: Path, capsys):
    fixtures = tmp_path / "f"
    write_json(
        fixtures / "deny.json",
        {
            "event": event(tool_input={"command": "cat .env"}),
            "expect": {"decision": "deny", "handlers": 1},
        },
    )
    code, out = run(
        capsys,
        "test",
        str(fixtures),
        "--settings",
        str(tree.project / ".claude" / "settings.json"),
        "--cwd",
        str(tree.project),
    )
    assert code == 0, out
    assert "settings: " in out


def test_test_runner_handles_missing_and_broken_fixtures(tmp_path: Path, capsys):
    code, out = run(capsys, "test", str(tmp_path / "nope"))
    assert code == 1 and "no such file" in out
    broken = tmp_path / "broken.json"
    broken.write_text("[]", encoding="utf-8")
    code, out = run(capsys, "test", str(broken), "--cwd", str(tmp_path))
    assert code == 1 and "fixture must be a JSON object" in out


def test_record_install_append_convert_uninstall(tree: Tree, tmp_path: Path, capsys, monkeypatch):
    target = tree.project / ".claude" / "settings.local.json"
    record_file = tmp_path / "rec" / "recorded.jsonl"
    code, out = run(
        capsys, "record", "--install", "--cwd", str(tree.project), "--record-file", str(record_file)
    )
    assert code == 0, out
    data = json.loads(target.read_text())
    assert "PreToolUse" in data["hooks"] and "PostToolUse" in data["hooks"]
    assert len(data["hooks"]["PreToolUse"]) == 2  # the existing mcp group plus the recorder
    recorder = data["hooks"]["PostToolUse"][0]["hooks"][0]
    assert (
        recorder["args"] == ["record", "--append", str(record_file)]
        and recorder["type"] == "command"
    )
    # idempotent
    code, out = run(
        capsys, "record", "--install", "--cwd", str(tree.project), "--record-file", str(record_file)
    )
    assert code == 0 and "(0 added)" in out
    assert len(json.loads(target.read_text())["hooks"]["PostToolUse"]) == 1

    monkeypatch.setattr(
        "sys.stdin", __import__("io").StringIO(json.dumps(event(tool_input={"command": "ls"})))
    )
    code, out = run(capsys, "record", "--append", str(record_file))
    assert code == 0 and out == ""
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("not json"))
    assert main(["record", "--append", str(record_file)]) == 0
    lines = record_file.read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["event"]["tool_name"] == "Bash"
    assert "raw" in json.loads(lines[1])

    out_dir = tmp_path / "fixtures"
    code, out = run(capsys, "record", "--to", str(out_dir), "--from", str(record_file))
    assert code == 0 and "wrote 1 fixture(s)" in out and "skipped 1" in out
    fixture = json.loads((out_dir / "001-PreToolUse-Bash.json").read_text())
    assert fixture["expect"] == {"decision": "none"} and fixture["event"]["tool_name"] == "Bash"

    code, out = run(capsys, "record", "--uninstall", "--cwd", str(tree.project))
    assert code == 0 and "removed 2" in out
    data = json.loads(target.read_text())
    assert "PostToolUse" not in data["hooks"] and len(data["hooks"]["PreToolUse"]) == 1


def test_record_to_without_recordings(tmp_path: Path, capsys):
    code, out = run(
        capsys, "record", "--to", str(tmp_path / "x"), "--from", str(tmp_path / "missing.jsonl")
    )
    assert code == 1 and "nothing recorded" in out


def _recordings(tmp_path, rows):
    source = tmp_path / "recordings.jsonl"
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return source


def _recorded(name="PostToolUse", tool="Bash", command="ls", time="2026-10-01T00:00:00Z"):
    return {
        "recorded_at": time,
        "event": {"hook_event_name": name, "tool_name": tool, "tool_input": {"command": command}},
    }


def test_record_to_deduplicates_payload_identity_and_repeat_conversion(tmp_path, capsys):
    first = _recorded()
    duplicate = _recorded(time="2026-10-02T00:00:00Z")
    first["event"]["tool_input"] = {"command": "ls", "timeout": 1}
    duplicate["event"]["tool_input"] = {"timeout": 1, "command": "ls"}
    duplicate["event"].update(session_id="other", cwd="/elsewhere", tool_response="changed")
    source = _recordings(tmp_path, [first, duplicate, _recorded(command="pwd")])
    output = tmp_path / "fixtures"
    args = ["record", "--to", str(output), "--from", str(source)]
    code, text = run(capsys, *args)
    assert code == 0 and "wrote 2 fixture(s)" in text and "duplicates 1" in text
    originals = {p.name: p.read_bytes() for p in output.iterdir()}
    code, text = run(capsys, *args)
    assert code == 0 and "wrote 0 fixture(s)" in text and "duplicates 3" in text
    assert {p.name: p.read_bytes() for p in output.iterdir()} == originals
    code, text = run(capsys, *args, "--keep-duplicates")
    assert code == 0 and "wrote 3 fixture(s)" in text
    assert len(list(output.glob("*.json"))) == 5


@pytest.mark.parametrize(
    "flags,expected",
    [
        (["--event", "PreToolUse"], ["PreToolUse-Bash"]),
        (["--tool", "Read"], ["PostToolUse-Read"]),
        (
            ["--event", "PreToolUse", "--event", "PostToolUse", "--tool", "Bash", "--tool", "Read"],
            [
                "PostToolUse-Bash",
                "PreToolUse-Bash",
                "PostToolUse-Read",
            ],
        ),
        (["--since", "2026-10-01T00:00:00Z"], ["PreToolUse-Bash", "PostToolUse-Read"]),
        (
            ["--event", "PostToolUse", "--tool", "Read", "--since", "2026-10-01T00:00:00Z"],
            ["PostToolUse-Read"],
        ),
    ],
)
def test_record_to_filters(tmp_path, capsys, flags, expected):
    rows = [
        _recorded(time="2026-09-30T23:59:59Z"),
        _recorded(name="PreToolUse"),
        _recorded(tool="Read", time="2026-10-01T03:00:00+02:00"),
    ]
    source = _recordings(tmp_path, rows)
    output = tmp_path / "fixtures"
    code, text = run(capsys, "record", "--to", str(output), "--from", str(source), *flags)
    assert code == 0, text
    assert [p.stem.split("-", 1)[1] for p in sorted(output.iterdir())] == expected


def test_record_to_since_filters_before_dedup_and_rejects_invalid_dates(tmp_path, capsys):
    source = _recordings(
        tmp_path,
        [
            _recorded(time="2026-09-30T23:59:59Z"),
            _recorded(),
            _recorded(command="missing", time=None),
            _recorded(command="bad", time="bad"),
        ],
    )
    output = tmp_path / "fixtures"
    args = ["record", "--to", str(output), "--from", str(source)]
    code, text = run(capsys, *args, "--since", "2026-10-01T00:00:00Z")
    assert code == 0 and "wrote 1 fixture(s)" in text and "filtered 3" in text
    for date in ("bad", "2026-10-01T00:00:00"):
        assert main([*args, "--since", date]) == 2
    assert (
        main(["record", "--install", "--keep-duplicates", "--file", str(tmp_path / "settings")])
        == 2
    )


def test_record_to_sparse_numbering_preserves_existing_files(tmp_path, capsys):
    output = tmp_path / "fixtures"
    output.mkdir()
    existing = output / "009-PostToolUse-Bash.json"
    existing.write_text('{"hand_edited":true}', encoding="utf-8")
    (output / "notes.json").write_text("not json", encoding="utf-8")
    source = _recordings(tmp_path, [_recorded()])
    assert run(capsys, "record", "--to", str(output), "--from", str(source))[0] == 0
    assert (output / "010-PostToolUse-Bash.json").is_file()
    assert existing.read_text() == '{"hand_edited":true}'


def test_record_install_into_explicit_file_and_user_scope(tree: Tree, tmp_path: Path, capsys):
    explicit = tmp_path / "custom.json"
    code, out = run(capsys, "record", "--install", "--file", str(explicit), "--events", "Stop")
    assert code == 0 and "Stop" in json.loads(explicit.read_text())["hooks"]
    code, out = run(capsys, "record", "--install", "--scope", "user", "--home", str(tree.home))
    assert code == 0
    assert (
        "PreToolUse" in json.loads((tree.home / ".claude" / "settings.json").read_text())["hooks"]
    )
    code, out = run(capsys, "record", "--install", "--file", str(explicit), "--events", "Nope")
    assert code == 2 and "unknown event" in out
