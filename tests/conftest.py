"""Shared fixtures: a fake home and project with settings files and small Python handlers."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
PY = sys.executable

DENY_ENV = """\
import json, sys
event = json.load(sys.stdin)
command = (event.get("tool_input") or {}).get("command", "")
if ".env" in command:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
        "permissionDecision": "deny", "permissionDecisionReason": "secret file"}}))
"""

CONTEXT = """\
import json, os, sys
event = json.load(sys.stdin)
print(json.dumps({"hookSpecificOutput": {"hookEventName": event["hook_event_name"],
    "additionalContext": "ctx dry_run=" + os.environ.get("CC_HOOKS_DRY_RUN", "unset")
    + " project=" + os.environ.get("CLAUDE_PROJECT_DIR", "unset")}}))
"""

EXIT2 = """\
import sys
sys.stderr.write("blocked by exit2\\n")
sys.exit(2)
"""

BAD = """\
import sys
sys.stderr.write("boom\\n")
sys.exit(1)
"""

SIDE_EFFECT = """\
import os, pathlib, sys
sys.stdin.read()
if not os.environ.get("CC_HOOKS_DRY_RUN"):
    pathlib.Path(__file__).with_name("marker.txt").write_text("ran")
"""

PLUGIN_ASK = """\
import json, os, sys
sys.stdin.read()
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "plugin asks from " + os.environ.get("CLAUDE_PLUGIN_ROOT", "?")}}))
"""


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def handler(script: str, *, matcher: str | None = None, **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"type": "command", "command": PY, "args": [script], **extra}
    group: dict[str, Any] = {"hooks": [entry]}
    if matcher is not None:
        group["matcher"] = matcher
    return group


def event(name: str = "PreToolUse", **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": "abc123",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": "/tmp/demo",
        "permission_mode": "default",
        "hook_event_name": name,
    }
    if name in (
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "PermissionRequest",
        "PermissionDenied",
    ):
        base.update(
            {"tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_use_id": "toolu_1"}
        )
    if name == "PostToolUse":
        base["tool_response"] = {"stdout": "", "stderr": "", "interrupted": False, "isImage": False}
    if name == "PostToolUseFailure":
        base["error"] = "Exit code 1"
    if name == "PermissionDenied":
        base["reason"] = "[Test]"
    if name == "Stop":
        base["stop_hook_active"] = False
    if name == "SessionStart":
        base["source"] = "startup"
    base.update(fields)
    return base


@dataclass
class Tree:
    home: Path
    project: Path
    hooks: Path
    plugin_root: Path

    def script(self, name: str) -> str:
        return str(self.hooks / name)


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Tree:
    """user, project, local and plugin settings plus the handlers they reference."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_PLUGIN_CACHE_DIR", raising=False)
    monkeypatch.delenv("CC_HOOKS_DRY_RUN", raising=False)
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    (project / "src").mkdir()
    hooks = project / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    for name, body in {
        "deny_env.py": DENY_ENV,
        "context.py": CONTEXT,
        "exit2.py": EXIT2,
        "bad.py": BAD,
        "side_effect.py": SIDE_EFFECT,
    }.items():
        (hooks / name).write_text(body, encoding="utf-8")

    plugin_root = home / ".claude" / "plugins" / "cache" / "local" / "demo" / "1.0.0"
    (plugin_root / "scripts").mkdir(parents=True)
    (plugin_root / "scripts" / "plugin_hook.py").write_text(PLUGIN_ASK, encoding="utf-8")
    write_json(
        plugin_root / "hooks" / "hooks.json",
        {
            "description": "demo plugin",
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": PY,
                                "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/plugin_hook.py"],
                                "timeout": 10,
                            }
                        ],
                    }
                ]
            },
        },
    )
    write_json(
        home / ".claude" / "plugins" / "installed_plugins.json",
        {
            "version": 2,
            "plugins": {
                "demo@local": [
                    {"scope": "user", "installPath": str(plugin_root), "version": "1.0.0"}
                ],
                "ghost@local": [
                    {"scope": "user", "installPath": str(home / "nowhere"), "version": "0"}
                ],
            },
        },
    )
    write_json(
        home / ".claude" / "settings.json",
        {
            "enabledPlugins": {"demo@local": True, "ghost@local": True},
            "hooks": {
                "PreToolUse": [
                    handler("${CLAUDE_PROJECT_DIR}/.claude/hooks/context.py", matcher="Bash"),
                    # identical to the project handler below: runs once
                    handler("${CLAUDE_PROJECT_DIR}/.claude/hooks/deny_env.py", matcher="Bash"),
                ],
                "Notification": [handler(str(hooks / "context.py"), matcher="idle_prompt")],
            },
        },
    )
    write_json(
        project / ".claude" / "settings.json",
        {
            "hooks": {
                "PreToolUse": [
                    handler("${CLAUDE_PROJECT_DIR}/.claude/hooks/deny_env.py", matcher="Bash"),
                    handler(str(hooks / "context.py"), matcher="Edit|Write"),
                    handler(str(hooks / "context.py"), matcher="Bash", **{"if": "Bash(git *)"}),
                ],
                "PostToolUse": [handler(str(hooks / "context.py"), matcher="*")],
                "Stop": [handler(str(hooks / "exit2.py"))],
                "SessionStart": [
                    {"hooks": [{"type": "prompt", "prompt": "Summarise $ARGUMENTS", "timeout": 30}]}
                ],
            }
        },
    )
    write_json(
        project / ".claude" / "settings.local.json",
        {
            "hooks": {
                "PreToolUse": [handler(str(hooks / "context.py"), matcher="mcp__memory__.*")],
                "PostToolUseFailure": [handler(str(hooks / "bad.py"))],
            }
        },
    )
    return Tree(home=home, project=project, hooks=hooks, plugin_root=plugin_root)
