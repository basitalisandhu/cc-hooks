"""The three example hooks behave as their docstrings say, run as real subprocesses."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from cc_hooks.cli import main

from .conftest import EXAMPLES, PY, ROOT, event


def run_hook(name: str, payload: dict, **env: str) -> subprocess.CompletedProcess[str]:
    environment = {k: v for k, v in os.environ.items() if k != "CC_HOOKS_DRY_RUN"}
    environment.update(env)
    return subprocess.run(
        [PY, str(EXAMPLES / name)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
        check=False,
    )


@pytest.mark.parametrize(
    "command, denied",
    [
        ("cat .env", True),
        ("less .env.production", True),
        ("printenv", True),
        ("echo $OPENWEATHER_API_KEY", True),
        ("ls -la", False),
        ("grep -n SECRET src/config.py", False),
        ("cat README.md", False),
        ('[ -n "$API_KEY" ] && echo set', False),
    ],
)
def test_deny_secrets(command: str, denied: bool):
    proc = run_hook("deny_secrets.py", event(tool_input={"command": command}))
    assert proc.returncode == 0, proc.stderr
    if denied:
        payload = json.loads(proc.stdout)["hookSpecificOutput"]
        assert payload["permissionDecision"] == "deny"
        assert "transcript" in payload["permissionDecisionReason"]
    else:
        assert proc.stdout == ""


def test_deny_secrets_ignores_other_tools_and_bad_input():
    proc = run_hook("deny_secrets.py", event(tool_name="Read", tool_input={"file_path": "/x/.env"}))
    assert proc.returncode == 0 and proc.stdout == ""
    proc = subprocess.run(
        [PY, str(EXAMPLES / "deny_secrets.py")], input="{", capture_output=True, text=True
    )
    assert proc.returncode == 1 and "deny_secrets" in proc.stderr


def test_session_context_reports_branch_and_title():
    payload = event("SessionStart", source="startup", cwd=str(ROOT))
    proc = run_hook("session_context.py", payload)
    assert proc.returncode == 0, proc.stderr
    if proc.stdout == "":
        pytest.skip("repository is not a git checkout")
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    assert "Current branch:" in out["additionalContext"]
    assert "Session source: startup" in out["additionalContext"]
    assert out["sessionTitle"].startswith(ROOT.name + "@")
    titled = run_hook("session_context.py", {**payload, "session_title": "mine"})
    assert "sessionTitle" not in json.loads(titled.stdout)["hookSpecificOutput"]


def test_session_context_is_silent_outside_git(tmp_path: Path):
    proc = run_hook("session_context.py", event("SessionStart", source="clear", cwd=str(tmp_path)))
    assert proc.returncode == 0 and proc.stdout == ""


def test_log_tool_use_writes_only_when_not_dry_run(tmp_path: Path):
    log = tmp_path / "tool-use.jsonl"
    payload = event(
        "PostToolUse",
        tool_name="Write",
        tool_input={"file_path": "/x", "content": ""},
        duration_ms=3,
    )
    proc = run_hook("log_tool_use.py", payload, CC_HOOKS_TOOL_LOG=str(log), CC_HOOKS_DRY_RUN="1")
    assert proc.returncode == 0 and proc.stdout == "" and "dry run" in proc.stderr
    assert not log.exists()
    proc = run_hook("log_tool_use.py", payload, CC_HOOKS_TOOL_LOG=str(log))
    assert proc.returncode == 0 and proc.stdout == ""
    record = json.loads(log.read_text().splitlines()[0])
    assert (
        record["tool_name"] == "Write"
        and record["file_path"] == "/x"
        and record["duration_ms"] == 3
    )
    proc = run_hook("log_tool_use.py", event("Stop"), CC_HOOKS_TOOL_LOG=str(log))
    assert proc.returncode == 0 and len(log.read_text().splitlines()) == 1


def test_example_fixtures_pass_with_example_settings(capsys, monkeypatch):
    """The README workflow: cc-hooks test examples/fixtures --settings examples/settings.json."""
    git = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        check=False,
    )
    if git.returncode != 0:
        pytest.skip("repository is not a git checkout; session-start.json expects branch context")
    monkeypatch.setenv("PATH", str(Path(PY).parent) + os.pathsep + os.environ.get("PATH", ""))
    code = main(
        [
            "test",
            str(EXAMPLES / "fixtures"),
            "--settings",
            str(EXAMPLES / "settings.json"),
            "--cwd",
            str(ROOT),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "summary: 5 passed, 0 failed" in out


def test_minimal_readme_example():
    proc = run_hook("minimal.py", event(tool_input={"command": "cat .env"}))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    proc = run_hook("minimal.py", event(tool_input={"command": "cat README.md"}))
    assert proc.returncode == 0 and proc.stdout == ""
    assert len((EXAMPLES / "minimal.py").read_text().splitlines()) <= 10
