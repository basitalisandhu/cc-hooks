"""Matcher resolution, settings merge order, plugins and the `if` field."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_hooks import parse_event
from cc_hooks.settings import (
    Handler,
    Source,
    collect,
    config_dir,
    discover,
    if_matches,
    managed_settings_paths,
    matcher_alternatives,
    matcher_kind,
    matches,
    plugins_root,
    project_root,
    resolve,
)

from .conftest import PY, Tree, event, handler, write_json


@pytest.mark.parametrize(
    "matcher, value, expected",
    [
        (None, "Bash", True),
        ("", "Bash", True),
        ("*", "anything", True),
        ("Bash", "Bash", True),
        ("Bash", "Bashful", False),
        ("Edit|Write", "Write", True),
        ("Edit, Write", "Edit", True),
        ("Edit|Write", "NotebookEdit", False),
        ("code-reviewer", "code-reviewer", True),
        ("^Notebook", "NotebookEdit", True),
        ("Edit.*", "NotebookEdit", True),
        ("^Edit$", "NotebookEdit", False),
        ("mcp__memory__.*", "mcp__memory__create_entities", True),
        ("mcp__memory", "mcp__memory__create_entities", False),
        ("mcp__.*__write.*", "mcp__db__write_rows", True),
        ("mcp__brave-search__.*", "mcp__brave-search__search", True),
        ("(", "x", False),
    ],
)
def test_matches_follows_the_matcher_patterns_table(matcher, value, expected):
    assert matches(matcher, value) is expected


def test_matcher_kinds_and_alternatives():
    assert matcher_kind("Bash") == "exact"
    assert matcher_kind("Edit|Write") == "exact"
    assert matcher_kind("mcp__.*") == "regex"
    assert matcher_kind("") == "all" and matcher_kind(None) == "all" and matcher_kind("*") == "all"
    assert matcher_alternatives("Edit, Write | Read") == ["Edit", "Write", "Read"]
    # FileChanged and StopFailure use the narrower exact set: hyphen, space and comma are regex
    assert matcher_kind(".envrc|.env", "FileChanged") == "regex"
    assert matcher_kind("envrc|env", "FileChanged") == "exact"
    assert matcher_kind("rate-limit", "StopFailure") == "regex"
    assert matcher_alternatives("a,b|c", "StopFailure") == ["a,b", "c"]
    assert matches("rate_limit|overloaded", "overloaded", "StopFailure")
    assert matches(None, None) and not matches("Bash", None)


def test_project_root_walks_up_to_git_or_claude(tmp_path: Path):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    deep = root / "a" / "b"
    deep.mkdir(parents=True)
    assert project_root(deep) == root.resolve()
    other = tmp_path / "plain" / "dir"
    other.mkdir(parents=True)
    assert project_root(other) == other.resolve()
    (tmp_path / "plain" / ".claude").mkdir()
    assert project_root(other) == (tmp_path / "plain").resolve()


def test_config_and_plugin_dirs_honour_environment(tmp_path: Path):
    assert config_dir(tmp_path, {}) == tmp_path / ".claude"
    assert config_dir(tmp_path, {"CLAUDE_CONFIG_DIR": str(tmp_path / "cfg")}) == tmp_path / "cfg"
    assert plugins_root(tmp_path, {}) == tmp_path / ".claude" / "plugins"
    custom = {"CLAUDE_CODE_PLUGIN_CACHE_DIR": str(tmp_path / "pl")}
    assert plugins_root(tmp_path, custom) == tmp_path / "pl"
    assert managed_settings_paths("linux")[0] == Path("/etc/claude-code/managed-settings.json")
    assert managed_settings_paths("darwin")[0].parts[1:3] == ("Library", "Application Support")
    assert managed_settings_paths("win32")[0].name == "managed-settings.json"


def test_discover_lists_sources_in_documented_order(tree: Tree):
    project, loaded, warnings = discover(
        tree.project / "src", home=tree.home, env={}, platform="linux"
    )
    assert project == tree.project.resolve()
    kinds = [item.source.kind for item in loaded]
    assert kinds[0] == "managed"
    assert kinds[-4:] == ["user", "project", "local", "plugin"]
    assert [item.source.plugin for item in loaded if item.source.kind == "plugin"] == ["demo@local"]
    assert any("ghost@local" in w and "not cached" in w for w in warnings)
    missing = [item for item in loaded if item.data is None]
    assert all(item.source.kind == "managed" for item in missing)


def test_collect_merges_in_order_and_runs_duplicates_once(tree: Tree):
    collection = collect(tree.project, home=tree.home, env={}, platform="linux")
    assert collection.warnings == [w for w in collection.warnings if "ghost" in w]
    pre = collection.handlers_for("PreToolUse")
    labels = [(h.source.label, h.matcher) for h in pre]
    assert labels == [
        ("user", "Bash"),  # context.py
        ("user", "Bash"),  # deny_env.py, first definition wins; the project copy is dropped
        ("project", "Edit|Write"),
        ("project", "Bash"),  # if Bash(git *)
        ("local", "mcp__memory__.*"),
        ("plugin:demo@local", "Bash"),
    ]
    assert sum(1 for h in pre if h.command == PY and "deny_env.py" in h.args[0]) == 1
    plugin = pre[-1]
    assert plugin.args == (str(tree.plugin_root / "scripts" / "plugin_hook.py"),)
    assert plugin.source.plugin_root == tree.plugin_root
    assert plugin.effective_timeout == 10
    assert "deny_env.py" in pre[1].resolved_command
    assert pre[1].args[0].startswith(str(tree.project))  # ${CLAUDE_PROJECT_DIR} substituted


def test_plugin_disabled_in_a_higher_precedence_source(tree: Tree):
    write_json(
        tree.project / ".claude" / "settings.local.json", {"enabledPlugins": {"demo@local": False}}
    )
    collection = collect(tree.project, home=tree.home, env={}, platform="linux")
    assert not [h for h in collection.handlers if h.source.kind == "plugin"]


def test_plugin_hooks_from_the_manifest_key(tree: Tree):
    manifest = tree.plugin_root / ".claude-plugin" / "plugin.json"
    write_json(
        manifest,
        {
            "name": "demo",
            "hooks": {
                "Stop": [
                    {"hooks": [{"type": "command", "command": 'echo "${CLAUDE_PLUGIN_ROOT}"'}]}
                ]
            },
        },
    )
    collection = collect(tree.project, home=tree.home, env={}, platform="linux")
    stops = [h for h in collection.handlers_for("Stop") if h.source.kind == "plugin"]
    assert len(stops) == 1
    assert stops[0].command == f'echo "{tree.plugin_root}"'


def test_disable_all_hooks_follows_precedence(tree: Tree):
    write_json(tree.home / ".claude" / "settings.json", {"disableAllHooks": True})
    assert collect(tree.project, home=tree.home, env={}, platform="linux").handlers == []
    # a project false overrides the user true
    data = json.loads((tree.project / ".claude" / "settings.json").read_text())
    data["disableAllHooks"] = False
    write_json(tree.project / ".claude" / "settings.json", data)
    collection = collect(tree.project, home=tree.home, env={}, platform="linux")
    assert collection.handlers and not collection.disable_all_hooks


def test_explicit_settings_file_only(tree: Tree):
    collection = collect(
        tree.project,
        home=tree.home,
        env={},
        settings_files=[tree.project / ".claude" / "settings.json"],
    )
    assert {h.source.kind for h in collection.handlers} == {"project"}
    assert collection.project_root == tree.project.resolve()
    assert len(collection.handlers_for("PreToolUse")) == 3


def test_invalid_settings_file_is_reported_not_fatal(tree: Tree):
    (tree.project / ".claude" / "settings.local.json").write_text("{oops", encoding="utf-8")
    collection = collect(tree.project, home=tree.home, env={}, platform="linux")
    assert any("settings.local.json" in w and "invalid JSON" in w for w in collection.warnings)
    assert collection.handlers_for("PostToolUseFailure") == []


def test_resolve_applies_matchers_in_source_order(tree: Tree):
    collection = collect(tree.project, home=tree.home, env={}, platform="linux")
    bash = parse_event(event("PreToolUse", tool_input={"command": "cat .env"}))
    resolved = resolve(collection.handlers_for("PreToolUse"), bash)
    assert [r.handler.source.label for r in resolved] == ["user", "user", "plugin:demo@local"]
    assert resolved[0].matched_by == "exact matcher 'Bash'"
    git = parse_event(event("PreToolUse", tool_input={"command": "git push"}))
    resolved = resolve(collection.handlers_for("PreToolUse"), git)
    assert [r.handler.source.label for r in resolved] == [
        "user",
        "user",
        "project",
        "plugin:demo@local",
    ]
    assert resolved[2].matched_by == "exact matcher 'Bash', if 'Bash(git *)'"
    mcp = parse_event(event("PreToolUse", tool_name="mcp__memory__read"))
    resolved = resolve(collection.handlers_for("PreToolUse"), mcp)
    assert [r.handler.source.label for r in resolved] == ["local"]
    assert resolved[0].matched_by == "regex matcher 'mcp__memory__.*'"
    edit = parse_event(
        event("PreToolUse", tool_name="Write", tool_input={"file_path": "/x", "content": ""})
    )
    assert [r.handler.matcher for r in resolve(collection.handlers_for("PreToolUse"), edit)] == [
        "Edit|Write"
    ]


def test_resolve_ignores_matchers_on_events_without_support(tree: Tree):
    source = Source("project", tree.project / ".claude" / "settings.json")
    stop_handler = Handler("Stop", "Bash", "command", source, command="true")
    resolved = resolve([stop_handler], parse_event(event("Stop")))
    assert len(resolved) == 1 and resolved[0].matched_by == "matcher ignored on this event"
    with_if = Handler("Stop", None, "command", source, command="true", if_rule="Bash(git *)")
    assert resolve([with_if], parse_event(event("Stop"))) == []


@pytest.mark.parametrize(
    "rule, command, expected",
    [
        ("Bash(git *)", "FOO=bar git push", True),
        ("Bash(git *)", "npm test && git push", True),
        ("Bash(rm *)", "echo $(rm -rf /)", True),
        ("Bash(rm *)", "echo $(date)", False),
        ("Bash(git push *)", "echo $(date)", True),
        ("Bash(git *)", "git push", True),
        ("Bash(git *)", "echo $(git log)", True),
        ("Bash", "anything", True),
        ("Edit(*.ts)", "ignored", False),
    ],
)
def test_if_rules_follow_the_bash_matching_table(rule, command, expected):
    bash = parse_event(event("PreToolUse", tool_input={"command": command}))
    assert if_matches(rule, bash) is expected


@pytest.mark.parametrize(
    "rule, expected",
    [
        ("Edit(src/**)", True),
        ("Edit(*.ts)", True),
        ("Edit(*.py)", False),
        ("Edit(other/**)", False),
        ("Write(*.ts)", False),
    ],
)
def test_if_rules_for_windows_file_paths(rule, expected):
    edit = parse_event(
        event(
            "PreToolUse",
            tool_name="Edit",
            cwd=r"C:\project",
            tool_input={"file_path": r"C:\project\src\index.ts"},
        )
    )
    assert if_matches(rule, edit) is expected


def test_if_rules_for_file_tools_and_domains():
    edit = parse_event(
        event(
            "PreToolUse",
            tool_name="Edit",
            cwd="/tmp/demo",
            tool_input={"file_path": "/tmp/demo/src/a.ts"},
        )
    )
    assert if_matches("Edit(*.ts)", edit) is True
    assert if_matches("Edit(src/**)", edit) is True
    assert if_matches("Edit(*.py)", edit) is False
    assert if_matches("Write(*.ts)", edit) is False
    fetch = parse_event(
        event(
            "PreToolUse",
            tool_name="WebFetch",
            tool_input={"url": "https://docs.example.com/a", "prompt": "x"},
        )
    )
    assert if_matches("WebFetch(domain:docs.example.com)", fetch) is True
    assert if_matches("WebFetch(domain:other.example.com)", fetch) is False
    assert if_matches("not a rule (", fetch) is None
    assert (
        if_matches(
            "mcp__github__*", parse_event(event("PreToolUse", tool_name="mcp__github__search"))
        )
        is True
    )


def test_handler_defaults_from_the_docs():
    source = Source("user", Path("/x"))
    assert Handler("PreToolUse", None, "command", source, command="x").effective_timeout == 600
    assert Handler("UserPromptSubmit", None, "command", source, command="x").effective_timeout == 30
    assert Handler("MessageDisplay", None, "http", source, raw={"url": "u"}).effective_timeout == 10
    assert Handler("SessionEnd", None, "command", source, command="x").effective_timeout == 1.5
    assert Handler("Stop", None, "prompt", source, raw={"prompt": "p"}).effective_timeout == 30
    assert Handler("Stop", None, "agent", source, raw={"prompt": "p"}).effective_timeout == 60
    assert Handler("Stop", None, "command", source, command="x", timeout=7).effective_timeout == 7
    shell = Handler("Stop", None, "command", source, command="a b")
    assert shell.exec_argv is None and shell.resolved_command == "a b"
    execform = Handler("Stop", None, "command", source, command="a", args=("b c",))
    assert execform.exec_argv == ["a", "b c"] and execform.resolved_command == "a 'b c'"
    http = Handler("Stop", None, "http", source, raw={"url": "https://h/x"})
    assert http.resolved_command == "POST https://h/x"
    assert handler("x")["hooks"][0]["type"] == "command"
