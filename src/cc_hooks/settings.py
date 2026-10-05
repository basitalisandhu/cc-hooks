"""Load and merge hook settings and resolve matchers the way Claude Code documents it.

Sources, in the order ``cc-hooks explain`` lists them:

1. managed settings (``managed-settings.json`` and ``managed-settings.d/*.json`` in the
   system directory for the platform),
2. ``~/.claude/settings.json`` (or ``$CLAUDE_CONFIG_DIR/settings.json``),
3. ``<project root>/.claude/settings.json``,
4. ``<project root>/.claude/settings.local.json``,
5. ``hooks/hooks.json`` (and the ``hooks`` manifest key) of every enabled plugin recorded
   in ``<plugins root>/installed_plugins.json``.

Hook entries merge across levels rather than replacing each other. A handler defined
identically in more than one settings file runs once; a plugin's copy stays separate.
Hooks declared in skill or subagent frontmatter exist only while those components are
active in a session, so they are out of scope here.

Reference: https://code.claude.com/docs/en/hooks ("Configuration", "Matcher patterns",
"Hook handler fields") and https://code.claude.com/docs/en/plugins/loading, 2026-10-03.
"""

from __future__ import annotations

import contextlib
import fnmatch
import json
import os
import re
import shlex
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .events import EVENTS, TOOL_EVENTS, Event

__all__ = [
    "Collection",
    "Handler",
    "Loaded",
    "Resolved",
    "SettingsError",
    "Source",
    "collect",
    "config_dir",
    "discover",
    "if_matches",
    "managed_settings_paths",
    "matcher_alternatives",
    "matcher_kind",
    "matches",
    "merged_value",
    "parse_handlers",
    "plugins_root",
    "project_root",
    "resolve",
]

SOURCE_KINDS: tuple[str, ...] = ("managed", "user", "project", "local", "plugin")
# Precedence for scalar keys such as disableAllHooks and enabledPlugins, lowest first.
PRECEDENCE: tuple[str, ...] = ("user", "project", "local", "managed")
PLACEHOLDERS: tuple[str, ...] = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_DATA")

_DEFAULT_TIMEOUTS = {
    "command": 600.0,
    "http": 600.0,
    "mcp_tool": 600.0,
    "prompt": 30.0,
    "agent": 60.0,
}
_SHORT_TIMEOUT_EVENTS = {"UserPromptSubmit": 30.0, "PreModelSwitch": 30.0, "PostModelSwitch": 30.0}


class SettingsError(ValueError):
    """Raised when a settings file exists but cannot be used."""


@dataclass(frozen=True)
class Source:
    """One file hooks were read from."""

    kind: str
    path: Path
    plugin: str | None = None
    plugin_root: Path | None = None

    @property
    def label(self) -> str:
        return f"plugin:{self.plugin}" if self.plugin else self.kind


@dataclass(frozen=True)
class Loaded:
    source: Source
    data: dict[str, Any] | None  # None when the file does not exist


@dataclass(frozen=True)
class Handler:
    """One hook handler after placeholder substitution."""

    event: str
    matcher: str | None
    type: str
    source: Source
    command: str | None = None
    args: tuple[str, ...] | None = None
    timeout: float | None = None
    if_rule: str | None = None
    is_async: bool = False
    status_message: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def exec_argv(self) -> list[str] | None:
        """The argument vector for exec form (``args`` present), else None (shell form)."""
        if self.type == "command" and self.args is not None and self.command:
            return [self.command, *self.args]
        return None

    @property
    def resolved_command(self) -> str:
        """What this handler runs, as one line."""
        if self.type == "command":
            argv = self.exec_argv
            return shlex.join(argv) if argv else (self.command or "")
        if self.type == "http":
            return f"POST {self.raw.get('url', '')}"
        if self.type == "mcp_tool":
            return f"{self.raw.get('server', '')} tool {self.raw.get('tool', '')}"
        if self.type in ("prompt", "agent"):
            prompt = str(self.raw.get("prompt", ""))
            return prompt if len(prompt) <= 80 else prompt[:77] + "..."
        return ""

    @property
    def effective_timeout(self) -> float:
        """Seconds before Claude Code cancels the hook, applying the documented defaults."""
        if self.timeout is not None:
            return float(self.timeout)
        if self.type in ("command", "http", "mcp_tool"):
            if self.event == "MessageDisplay":
                return 10.0
            if self.event == "SessionEnd":
                return 1.5
            if self.event in _SHORT_TIMEOUT_EVENTS:
                return _SHORT_TIMEOUT_EVENTS[self.event]
        return _DEFAULT_TIMEOUTS.get(self.type, 600.0)

    @property
    def identity(self) -> tuple[Any, ...]:
        """Equality key used to run a handler once when several settings files define it."""
        return (
            self.event,
            self.matcher or "",
            self.type,
            self.command,
            self.args,
            self.if_rule,
            self.raw.get("url"),
            self.raw.get("server"),
            self.raw.get("tool"),
            self.raw.get("prompt"),
        )


@dataclass(frozen=True)
class Resolved:
    handler: Handler
    matched_by: str


@dataclass
class Collection:
    """Everything ``collect()`` found."""

    project_root: Path
    handlers: list[Handler]
    loaded: list[Loaded]
    warnings: list[str]
    disable_all_hooks: bool = False

    @property
    def sources(self) -> list[Source]:
        return [item.source for item in self.loaded]

    def handlers_for(self, event: str) -> list[Handler]:
        return [handler for handler in self.handlers if handler.event == event]


# --------------------------------------------------------------------------- locations


def config_dir(home: Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    """``$CLAUDE_CONFIG_DIR`` when set, else ``<home>/.claude``."""
    environ = os.environ if env is None else env
    configured = environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    return (home if home is not None else Path.home()) / ".claude"


def plugins_root(home: Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    """``$CLAUDE_CODE_PLUGIN_CACHE_DIR`` when set, else ``<config dir>/plugins``."""
    environ = os.environ if env is None else env
    configured = environ.get("CLAUDE_CODE_PLUGIN_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    return config_dir(home, env) / "plugins"


def managed_settings_paths(platform: str | None = None) -> list[Path]:
    """The managed settings file and its drop-in directory for the platform."""
    name = platform or sys.platform
    if name == "darwin":
        base = Path("/Library/Application Support/ClaudeCode")
    elif name.startswith("win"):
        base = Path(r"C:\Program Files\ClaudeCode")
    else:
        base = Path("/etc/claude-code")
    paths = [base / "managed-settings.json"]
    dropins = base / "managed-settings.d"
    if dropins.is_dir():
        paths.extend(sorted(p for p in dropins.glob("*.json") if not p.name.startswith(".")))
    return paths


def project_root(cwd: Path) -> Path:
    """The nearest ancestor (including cwd) holding ``.claude/`` or ``.git``, else cwd."""
    cwd = cwd.resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".claude").is_dir() or (candidate / ".git").exists():
            return candidate
    return cwd


def load_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SettingsError(f"{path}: {exc.strerror}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SettingsError(f"{path}: invalid JSON at line {exc.lineno}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise SettingsError(f"{path}: top level must be a JSON object")
    return data


def _load(source: Source, warnings: list[str]) -> Loaded:
    if not source.path.is_file():
        return Loaded(source, None)
    try:
        return Loaded(source, load_json(source.path))
    except SettingsError as exc:
        warnings.append(str(exc))
        return Loaded(source, None)


def merged_value(loaded: Sequence[Loaded], key: str, default: Any = None) -> Any:
    """The value left for ``key`` after settings precedence (managed highest)."""
    value = default
    for kind in PRECEDENCE:
        for item in loaded:
            if item.source.kind == kind and item.data is not None and key in item.data:
                value = item.data[key]
    return value


def enabled_plugins(loaded: Sequence[Loaded]) -> dict[str, bool]:
    """``enabledPlugins`` merged key by key, highest-precedence source winning."""
    merged: dict[str, bool] = {}
    for kind in PRECEDENCE:
        for item in loaded:
            if item.source.kind != kind or item.data is None:
                continue
            entries = item.data.get("enabledPlugins")
            if isinstance(entries, dict):
                for plugin_id, enabled in entries.items():
                    merged[str(plugin_id)] = bool(enabled)
    return merged


def _plugin_sources(
    enabled: Mapping[str, bool], root: Path, project: Path, warnings: list[str]
) -> list[Source]:
    record = root / "installed_plugins.json"
    if not record.is_file():
        return []
    try:
        data = load_json(record)
    except SettingsError as exc:
        warnings.append(str(exc))
        return []
    plugins = data.get("plugins", {})
    if not isinstance(plugins, dict):
        return []
    sources: list[Source] = []
    for plugin_id, entries in plugins.items():
        if not enabled.get(str(plugin_id)):
            continue
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            install_path = entry.get("installPath")
            if not isinstance(install_path, str):
                continue
            project_path = entry.get("projectPath")
            if isinstance(project_path, str) and Path(project_path).resolve() != project:
                continue
            plugin_root = Path(install_path)
            if not plugin_root.is_dir():
                warnings.append(f"plugin {plugin_id} is enabled but not cached at {plugin_root}")
                continue
            hooks_file = plugin_root / "hooks" / "hooks.json"
            if hooks_file.is_file():
                sources.append(Source("plugin", hooks_file, str(plugin_id), plugin_root))
            manifest = plugin_root / ".claude-plugin" / "plugin.json"
            if manifest.is_file():
                try:
                    hooks_key = load_json(manifest).get("hooks")
                except SettingsError as exc:
                    warnings.append(str(exc))
                    hooks_key = None
                if isinstance(hooks_key, str):
                    extra = (plugin_root / hooks_key).resolve()
                    if extra != hooks_file.resolve() and extra.is_file():
                        sources.append(Source("plugin", extra, str(plugin_id), plugin_root))
                elif isinstance(hooks_key, dict):
                    sources.append(Source("plugin", manifest, str(plugin_id), plugin_root))
    return sources


def discover(
    cwd: Path | None = None,
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    settings_files: Sequence[Path] | None = None,
    plugins: bool = True,
    walk_up: bool = True,
) -> tuple[Path, list[Loaded], list[str]]:
    """Find and load every settings source for ``cwd``.

    With ``settings_files`` only those files are read (as project settings), which is what
    ``cc-hooks test --settings`` uses to test a repository's hooks in isolation. The project
    root is then ``cwd`` when given, else the directory holding the first file (or its
    parent when that directory is ``.claude``).

    ``walk_up`` looks for the nearest ``.claude/`` or ``.git`` above ``cwd``, which is the
    right guess for a payload's ``cwd`` after Claude ran ``cd``. Pass ``walk_up=False`` when
    ``cwd`` is the directory the session started in, which is what ``${CLAUDE_PROJECT_DIR}``
    means.
    """
    warnings: list[str] = []
    base = Path(cwd) if cwd is not None else Path.cwd()
    if settings_files:
        first = Path(settings_files[0]).resolve()
        if cwd is not None:
            project = project_root(base) if walk_up else base.resolve()
        else:
            project = first.parent.parent if first.parent.name == ".claude" else first.parent
        loaded = [_load(Source("project", Path(p).resolve()), warnings) for p in settings_files]
        return project, loaded, warnings
    project = project_root(base) if walk_up else base.resolve()
    sources = [Source("managed", path) for path in managed_settings_paths(platform)]
    sources.append(Source("user", config_dir(home, env) / "settings.json"))
    sources.append(Source("project", project / ".claude" / "settings.json"))
    sources.append(Source("local", project / ".claude" / "settings.local.json"))
    loaded = [_load(source, warnings) for source in sources]
    if plugins:
        root = plugins_root(home, env)
        for source in _plugin_sources(enabled_plugins(loaded), root, project, warnings):
            loaded.append(_load(source, warnings))
    return project, loaded, warnings


# --------------------------------------------------------------------------- handlers


def _substitute(text: str, mapping: Mapping[str, str]) -> str:
    for name, value in mapping.items():
        text = text.replace("${" + name + "}", value).replace("$" + name, value)
    return text


def parse_handlers(
    data: Mapping[str, Any],
    source: Source,
    *,
    project: Path,
    plugins_dir: Path | None = None,
    warnings: list[str] | None = None,
) -> list[Handler]:
    """Turn a settings object's ``hooks`` key into handlers, in file order."""
    hooks = data.get("hooks")
    if hooks is None:
        return []
    if not isinstance(hooks, dict):
        if warnings is not None:
            warnings.append(f"{source.path}: 'hooks' must be an object")
        return []
    mapping = {"CLAUDE_PROJECT_DIR": str(project)}
    if source.plugin_root is not None:
        mapping["CLAUDE_PLUGIN_ROOT"] = str(source.plugin_root)
        data_root = plugins_dir if plugins_dir is not None else source.plugin_root.parent
        mapping["CLAUDE_PLUGIN_DATA"] = str(data_root / "data" / (source.plugin or ""))
    handlers: list[Handler] = []
    for event, groups in hooks.items():
        if event == "modules":
            continue
        if event not in EVENTS and warnings is not None:
            warnings.append(f"{source.path}: unknown hook event {event!r}")
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher")
            matcher = matcher if isinstance(matcher, str) else None
            entries = group.get("hooks")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                kind = str(entry.get("type", "command"))
                command = entry.get("command")
                command = _substitute(command, mapping) if isinstance(command, str) else None
                args = entry.get("args")
                args_tuple = (
                    tuple(_substitute(str(arg), mapping) for arg in args)
                    if isinstance(args, list)
                    else None
                )
                timeout = entry.get("timeout")
                handlers.append(
                    Handler(
                        event=str(event),
                        matcher=matcher,
                        type=kind,
                        source=source,
                        command=command,
                        args=args_tuple,
                        timeout=float(timeout) if isinstance(timeout, int | float) else None,
                        if_rule=entry.get("if") if isinstance(entry.get("if"), str) else None,
                        is_async=bool(entry.get("async", False)),
                        status_message=(
                            entry.get("statusMessage")
                            if isinstance(entry.get("statusMessage"), str)
                            else None
                        ),
                        raw=dict(entry),
                    )
                )
    return handlers


def collect(
    cwd: Path | None = None,
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    settings_files: Sequence[Path] | None = None,
    plugins: bool = True,
    walk_up: bool = True,
) -> Collection:
    """Discover, load, merge and de-duplicate every handler that applies to ``cwd``."""
    project, loaded, warnings = discover(
        cwd,
        home=home,
        env=env,
        platform=platform,
        settings_files=settings_files,
        plugins=plugins,
        walk_up=walk_up,
    )
    plugins_dir = plugins_root(home, env)
    handlers: list[Handler] = []
    seen: set[tuple[Any, ...]] = set()
    for item in loaded:
        if item.data is None:
            continue
        for handler in parse_handlers(
            item.data, item.source, project=project, plugins_dir=plugins_dir, warnings=warnings
        ):
            if handler.source.kind == "plugin":
                handlers.append(handler)
                continue
            if handler.identity in seen:
                continue
            seen.add(handler.identity)
            handlers.append(handler)
    disabled = bool(merged_value(loaded, "disableAllHooks", False))
    if disabled:
        managed_disabled = any(
            item.source.kind == "managed"
            and item.data is not None
            and item.data.get("disableAllHooks") is True
            for item in loaded
        )
        kept = [h for h in handlers if h.source.kind == "managed" and not managed_disabled]
        dropped = len(handlers) - len(kept)
        if dropped:
            warnings.append(f"disableAllHooks is true: {dropped} handler(s) will not run")
        handlers = kept
    return Collection(project, handlers, loaded, warnings, disabled)


# --------------------------------------------------------------------------- matchers

_EXACT_CHARS = re.compile(r"^[A-Za-z0-9_\- ,|]*$")
_EXACT_CHARS_NARROW = re.compile(r"^[A-Za-z0-9_|]*$")
_NARROW_EVENTS = frozenset({"FileChanged", "StopFailure"})


def matcher_kind(matcher: str | None, event: str | None = None) -> str:
    """``all``, ``exact`` or ``regex``, following the matcher patterns table."""
    if matcher is None or matcher == "" or matcher == "*":
        return "all"
    pattern = _EXACT_CHARS_NARROW if event in _NARROW_EVENTS else _EXACT_CHARS
    return "exact" if pattern.match(matcher) else "regex"


def matcher_alternatives(matcher: str, event: str | None = None) -> list[str]:
    """The exact strings an exact matcher lists (split on ``|``, and ``,`` where allowed)."""
    separators = "|" if event in _NARROW_EVENTS else "|,"
    parts = re.split("[" + re.escape(separators) + "]", matcher)
    return [part.strip() for part in parts if part.strip()]


def matches(matcher: str | None, value: str | None, event: str | None = None) -> bool:
    """Whether ``matcher`` fires for ``value`` under the documented rules.

    Regular expressions are evaluated with Python's ``re.search`` (unanchored, like
    JavaScript's ``RegExp.prototype.test``); the small syntax differences between the two
    engines are not modelled.
    """
    kind = matcher_kind(matcher, event)
    if kind == "all":
        return True
    if value is None:
        return False
    assert matcher is not None
    if kind == "exact":
        return value in matcher_alternatives(matcher, event)
    try:
        return re.search(matcher, value) is not None
    except re.error:
        return False


# --------------------------------------------------------------------------- `if` rules

_IF_RULE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_\-*]*)\s*(?:\((.*)\))?\s*$", re.S)
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*\s*")
_SPLIT_SUBCOMMANDS = re.compile(r"\|\||&&|;|\||\n")
_SUBSTITUTIONS = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")


def _subcommands(command: str) -> list[str]:
    parts: list[str] = []
    for match in _SUBSTITUTIONS.finditer(command):
        inner = match.group(1) if match.group(1) is not None else match.group(2)
        if inner:
            parts.append(inner)
    for part in _SPLIT_SUBCOMMANDS.split(command):
        part = part.strip()
        while part:
            stripped = _ASSIGNMENT.sub("", part, count=1)
            if stripped == part:
                break
            part = stripped.strip()
        if part:
            parts.append(part)
    return parts


def _bash_rule_matches(pattern: str, command: str) -> bool:
    words = pattern.split()
    beyond_name = len(words) > 1 and any(word != "*" for word in words[1:])
    if beyond_name and re.search(r"\$\(|`|\$[A-Za-z_{]", command):
        return True  # the docs: patterns beyond the command name run the hook anyway
    return any(fnmatch.fnmatchcase(part, pattern) for part in _subcommands(command))


def if_matches(rule: str, event: Event) -> bool | None:
    """Best-effort evaluation of a handler's ``if`` permission rule.

    Returns True or False when the rule can be evaluated, and None when it cannot, in
    which case Claude Code runs the hook anyway. On events other than the five tool
    events a handler with ``if`` never runs, so the result is False.
    """
    if event.hook_event_name not in TOOL_EVENTS:
        return False
    tool_name = getattr(event, "tool_name", None)
    tool_input = getattr(event, "tool_input", None)
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return None
    match = _IF_RULE.match(rule)
    if not match:
        return None
    tool, pattern = match.group(1), match.group(2)
    if not fnmatch.fnmatchcase(tool_name, tool):
        return False
    if pattern is None or pattern.strip() == "":
        return True
    pattern = pattern.strip()
    if tool_name in ("Bash", "PowerShell"):
        command = tool_input.get("command")
        if not isinstance(command, str):
            return None
        return _bash_rule_matches(pattern, command)
    if pattern.startswith("domain:"):
        url = tool_input.get("url")
        if not isinstance(url, str):
            return None
        return fnmatch.fnmatchcase(urlsplit(url).hostname or "", pattern[len("domain:") :])
    path = tool_input.get("file_path")
    if not isinstance(path, str):
        return None
    candidates = [path.replace("\\", "/")]
    if isinstance(event.cwd, str):
        with contextlib.suppress(ValueError):
            candidates.append(
                str(
                    Path(path.replace("\\", "/")).relative_to(event.cwd.replace("\\", "/"))
                ).replace("\\", "/")
            )
    expanded = os.path.expanduser(pattern)
    return any(
        fnmatch.fnmatchcase(c, expanded) or fnmatch.fnmatchcase(c, pattern) for c in candidates
    )


# --------------------------------------------------------------------------- resolving


def resolve(handlers: Sequence[Handler], event: Event) -> list[Resolved]:
    """The handlers that would run for ``event``, in source order, with the reason each matched."""
    spec = EVENTS.get(event.hook_event_name)
    value = event.matcher_value
    out: list[Resolved] = []
    for handler in handlers:
        if handler.event != event.hook_event_name:
            continue
        if spec is None or spec.matcher_field is None:
            note = "matcher ignored on this event" if handler.matcher else "no matcher"
        else:
            kind = matcher_kind(handler.matcher, event.hook_event_name)
            if not matches(handler.matcher, value, event.hook_event_name):
                continue
            note = "no matcher" if kind == "all" else f"{kind} matcher {handler.matcher!r}"
        if handler.if_rule is not None:
            verdict = if_matches(handler.if_rule, event)
            if verdict is False:
                continue
            note += f", if {handler.if_rule!r}" + (
                " (not evaluable, runs anyway)" if verdict is None else ""
            )
        out.append(Resolved(handler, note))
    return out
