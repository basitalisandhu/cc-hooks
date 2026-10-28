"""Property, regression and scaling tests: bad input gives one clear line, never a traceback."""

from __future__ import annotations

import io
import json
import random
import time
from pathlib import Path
from typing import Any

import pytest

from cc_hooks import EVENTS, HookInputError, parse_event, read_event
from cc_hooks.cli import main
from cc_hooks.settings import Handler, Source, _subcommands

from .conftest import event, write_json

NAMES = list(EVENTS)
FIELDS = [
    "session_id", "cwd", "hook_event_name", "tool_name", "tool_input", "tool_response", "prompt",
    "source", "matcher", "hooks", "type", "command", "args", "if", "timeout", "url", "headers",
    "disableAllHooks", "file_path", "agent_type", "permission_mode", "stop_hook_active",
]  # fmt: skip
STRINGS = ["", "Bash", "*", "mcp__.*", "(", "[", "cat .env", "Bash(cat *)", "/tmp", "PreToolUse"]


def value(r: random.Random, depth: int = 0) -> Any:
    c = r.randrange(9 if depth < 4 else 5)
    if c == 0:
        return r.choice([None, True, False])
    if c == 1:
        return r.randint(-5, 5)
    if c == 2:
        return r.choice(STRINGS)
    if c == 3:
        return 1.5
    if c == 4:
        return "s"
    if c in (5, 6):
        return [value(r, depth + 1) for _ in range(r.randrange(4))]
    return {r.choice(FIELDS + NAMES): value(r, depth + 1) for _ in range(r.randrange(6))}


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_parse_event_only_ever_raises_hook_input_error():
    r = random.Random(20261028)
    for _ in range(3000):
        payload = value(r)
        if isinstance(payload, dict) and r.random() < 0.8:
            payload["hook_event_name"] = r.choice(NAMES)
        for strict in (True, False):
            try:
                parsed = parse_event(payload, strict=strict)
            except HookInputError:
                continue
            _ = (parsed.matcher_value, getattr(parsed, "command", None))


def test_read_event_reports_deep_nesting_as_hook_input_error():
    with pytest.raises(HookInputError, match="nested too deeply"):
        read_event(io.StringIO("[" * 100000))


def test_random_settings_and_fixtures_never_crash_explain_or_test(tmp_path, capsys):
    r = random.Random(20261028)
    for i in range(150):
        root = tmp_path / str(i)
        home = root / "home"
        home.mkdir(parents=True)
        settings: Any = value(r)
        if r.random() < 0.7:
            entry = {"type": r.choice(["command", "http", "prompt"]), "command": value(r)}
            entry.update({"if": value(r), "args": value(r), "url": value(r), "prompt": value(r)})
            settings = {"hooks": {"PreToolUse": [{"matcher": value(r), "hooks": [entry]}]}}
        write_json(root / ".claude" / "settings.json", settings)
        payload = event(cwd=str(root))
        write_json(root / "event.json", payload)
        expect = value(r) if r.random() < 0.5 else {"decision": value(r), "handlers": value(r)}
        write_json(root / "fixture.json", {"event": payload, "expect": expect})
        common = ("--cwd", str(root), "--home", str(home))
        for argv in (
            ("explain", str(root / "event.json"), *common),
            ("explain", str(root / "event.json"), *common, "--json"),
            ("test", str(root / "fixture.json"), *common, "--no-run"),
        ):
            code, _out, err = run(capsys, *argv)
            assert code in (0, 1, 2), (i, argv, err)
            assert "internal error" not in err, (i, argv, err)


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"\xff\xfe{")
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 100000)
    good = tmp_path / "event.json"
    write_json(good, event(cwd=str(tmp_path)))
    (tmp_path / "home").mkdir()
    return {"bad": bad, "deep": deep, "good": good, "home": tmp_path / "home", "root": tmp_path}


@pytest.mark.parametrize(
    "kind, message",
    [("bad", "not UTF-8 text"), ("deep", "nested too deeply"), ("missing", "No such file")],
)
def test_explain_names_an_unreadable_payload(files, capsys, kind, message):
    path = files.get(kind, files["root"] / "missing.json")
    code, _out, err = run(capsys, "explain", str(path), "--home", str(files["home"]))
    assert code == 2
    assert err.startswith("cc-hooks explain: ") and message in err and "Traceback" not in err


@pytest.mark.parametrize(
    "kind, message", [("bad", "not UTF-8 text"), ("deep", "nested too deeply")]
)
def test_explain_reports_an_unreadable_settings_file(files, capsys, kind, message):
    common = ("--cwd", str(files["root"]), "--home", str(files["home"]))
    code, out, err = run(
        capsys, "explain", str(files["good"]), *common, "--settings", str(files[kind])
    )
    assert code in (0, 2)
    assert message in out + err and "Traceback" not in err
    write_json(files["root"] / ".claude" / "settings.json", {})
    (files["root"] / ".claude" / "settings.json").write_bytes(files[kind].read_bytes())
    code, _out, err = run(capsys, "explain", str(files["good"]), *common)
    assert code == 0
    assert message in out + err


def test_unhashable_handler_fields_do_not_crash_deduplication(files, capsys):
    hook = {"type": "prompt", "prompt": {"not": "a string"}, "url": ["x"]}
    write_json(
        files["root"] / ".claude" / "settings.json", {"hooks": {"PreToolUse": [{"hooks": [hook]}]}}
    )
    common = ("--cwd", str(files["root"]), "--home", str(files["home"]))
    code, _out, err = run(capsys, "explain", str(files["good"]), *common)
    assert code == 0, err


@pytest.mark.parametrize(
    "expect, message",
    [
        ({"decision": "denied"}, "expect.decision must be one of"),
        ({"decision": 2}, "expect.decision must be one of"),
        ({"handlers": "one"}, "expect.handlers must be a whole number"),
        ({"handlers": True}, "expect.handlers must be a whole number"),
        ({"exit_code": -1}, "expect.exit_code must be a whole number"),
        ({"reason_contains": ["a"]}, "expect.reason_contains must be a string"),
    ],
)
def test_fixture_expectations_are_validated(files, capsys, expect, message):
    write_json(files["root"] / "f.json", {"event": event(cwd=str(files["root"])), "expect": expect})
    common = ("--cwd", str(files["root"]), "--home", str(files["home"]), "--no-run")
    code, out, err = run(capsys, "test", str(files["root"] / "f.json"), *common)
    assert code == 1
    assert message in out and "Traceback" not in err


def test_fixture_files_that_are_not_utf8_or_too_deep_are_errors(files, capsys):
    folder = files["root"] / "fixtures"
    folder.mkdir()
    (folder / "a.json").write_bytes(files["bad"].read_bytes())
    (folder / "b.json").write_text("[" * 100000)
    write_json(folder / "c.json", {"event": event(cwd=str(files["root"])), "expect": {}})
    common = ("--cwd", str(files["root"]), "--home", str(files["home"]), "--no-run")
    code, out, _err = run(capsys, "test", str(folder), *common)
    assert code == 1
    assert "a.json: not UTF-8 text" in out and "b.json: JSON is nested too deeply" in out


def test_unwritable_report_path_is_one_line(files, capsys):
    write_json(files["root"] / "f.json", {"event": event(cwd=str(files["root"])), "expect": {}})
    report = files["root"] / "missing" / "r.json"
    common = ("--cwd", str(files["root"]), "--home", str(files["home"]), "--no-run")
    code, _out, err = run(
        capsys, "test", str(files["root"] / "f.json"), *common, "--report", str(report)
    )
    assert code == 2
    assert err.startswith("cc-hooks: cannot read or write ") and "Traceback" not in err


def test_record_install_and_uninstall_cope_with_odd_settings(files, capsys):
    target = files["root"] / "s.json"
    for content, code_wanted in [
        ('{"hooks": {"PreToolUse": [5, {"hooks": 3}]}}', 0),
        (b"\xff\xfe", 2),
        ("[" * 100000, 2),
    ]:
        target.write_bytes(content if isinstance(content, bytes) else content.encode())
        code, _out, err = run(capsys, "record", "--install", "--file", str(target))
        assert code == code_wanted, err
        assert "Traceback" not in err
    missing = files["root"] / "never-created.json"
    code, out, _err = run(capsys, "record", "--uninstall", "--file", str(missing))
    assert code == 0 and "does not exist" in out and not missing.exists()


def test_record_conversion_skips_lines_nested_too_deeply(files, capsys):
    recorded = files["root"] / "rec.jsonl"
    good = json.dumps({"recorded_at": "2026-10-28T00:00:00+00:00", "event": event()})
    recorded.write_text("[" * 100000 + "\n" + good + "\n")
    code, out, err = run(
        capsys, "record", "--to", str(files["root"] / "out"), "--from", str(recorded)
    )
    assert code == 0, err
    assert "wrote 1 fixture(s)" in out and "skipped 1" in out


def test_http_handler_lines_show_url_and_redacted_headers():
    source = Source("project", Path("/p/.claude/settings.json"))
    raw = {
        "url": "https://user:hunter2@hooks.example/x",
        "headers": {"Authorization": "Bearer abc"},
    }
    line = Handler("Stop", None, "http", source, raw=raw).resolved_command
    assert (
        line
        == "POST https://user:<redacted>@hooks.example/x with headers Authorization: <redacted>"
    )
    assert "hunter2" not in line and "abc" not in line
    plain = Handler("Stop", None, "http", source, raw={"url": "https://h/x"})
    assert plain.resolved_command == "POST https://h/x"


def test_http_handler_urls_redact_every_query_value():
    source = Source("project", Path("/p/.claude/settings.json"))

    def line(url: str) -> str:
        return Handler("Stop", None, "http", source, raw={"url": url}).resolved_command

    assert (
        line("https://h/x?token=abc&team=t") == "POST https://h/x?token=<redacted>&team=<redacted>"
    )
    assert line("https://h/x?a=1&&b=2") == "POST https://h/x?a=<redacted>&&b=<redacted>"
    assert line("https://h/x?flag") == "POST https://h/x?flag=<redacted>"
    assert line("https://h/x") == "POST https://h/x"
    assert line("http://[::1/x?token=abc") == "POST http://[::1/x?token=abc"
    assert "abc" not in line("https://u:pw@h/x?token=abc")


def test_cc_hooks_test_lists_http_handlers_as_would_call(files, capsys):
    hook = {
        "type": "http",
        "url": "https://hooks.example/pre",
        "headers": {"X-Token": "secret-value"},
    }
    write_json(
        files["root"] / ".claude" / "settings.json", {"hooks": {"PreToolUse": [{"hooks": [hook]}]}}
    )
    write_json(files["root"] / "f.json", {"event": event(cwd=str(files["root"])), "expect": {}})
    common = ("--cwd", str(files["root"]), "--home", str(files["home"]))
    code, out, _err = run(capsys, "test", str(files["root"] / "f.json"), *common)
    assert code == 0
    assert "would call: POST https://hooks.example/pre with headers X-Token: <redacted>" in out
    assert "secret-value" not in out
    code, out, _err = run(capsys, "explain", str(files["good"]), *common, "--json")
    assert "secret-value" not in out and "X-Token: <redacted>" in out


def _seconds(fn: Any) -> float:
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    return max(best, 0.0005)


def test_leading_assignments_are_stripped_in_linear_time():
    # One re.sub per assignment copied the rest of the command each time: quadratic.
    _subcommands.cache_clear()
    small = _seconds(lambda: _subcommands.__wrapped__("A=1 " * 5000 + "ls"))
    large = _seconds(lambda: _subcommands.__wrapped__("A=1 " * 50000 + "ls"))
    assert _subcommands("FOO=1 BAR=$(x) git push; X=2 ls") == ("x", "git push", "ls")
    assert large / small < 30, f"5,000 assignments: {small:.4f}s, 50,000: {large:.4f}s"


def test_record_conversion_scales_linearly(tmp_path, capsys):
    def make(n: int) -> Path:
        path = tmp_path / f"rec{n}.jsonl"
        lines = []
        for i in range(n):
            payload = event(tool_input={"command": f"ls {i % (n // 2)}"})
            lines.append(json.dumps({"recorded_at": "2026-10-28T00:00:00+00:00", "event": payload}))
        path.write_text("\n".join(lines) + "\n")
        return path

    small_file, large_file = make(500), make(5000)
    counter = iter(range(100))

    def convert(path: Path) -> None:
        main(["record", "--to", str(tmp_path / f"out{next(counter)}"), "--from", str(path)])
        capsys.readouterr()

    small = _seconds(lambda: convert(small_file))
    large = _seconds(lambda: convert(large_file))
    assert large / small < 30, f"500 lines: {small:.3f}s, 5,000 lines: {large:.3f}s"
