# cc-hooks: typed Python SDK and offline test runner for Claude Code hooks

One frozen dataclass per documented hook event, decision builders that print exactly the JSON and exit codes the [hooks reference](https://code.claude.com/docs/en/hooks) documents, and a `cc-hooks` command that tells you which handlers would run for a payload, replays recorded payloads through them without side effects, and records real payloads as fixtures. Standard library only, Python 3.11 or newer.

[![CI](https://github.com/basitalisandhu/cc-hooks/actions/workflows/ci.yml/badge.svg)](https://github.com/basitalisandhu/cc-hooks/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

## Why

A Claude Code hook is a program that reads a JSON payload on stdin and answers with JSON on stdout or an exit code. The [hooks reference](https://code.claude.com/docs/en/hooks) lists 33 events (as read on 2026-10-03), each with its own input fields, its own decision shape (`hookSpecificOutput.permissionDecision` on `PreToolUse`, a top-level `decision: "block"` on `Stop`, `decision.behavior` on `PermissionRequest`, nothing at all on `Notification`) and its own answer to "what does exit code 2 do". Most hooks in the wild parse that payload by hand with `json.load(sys.stdin)` and copy the output shape from an example, which is how a hook ends up printing `additionalContext` at the top level where Claude Code silently ignores it, or exiting 1 to block when only exit 2 blocks.

On the Python side nothing current covers that surface. The only Python SDK on PyPI, [cchooks](https://pypi.org/project/cchooks/), covers 9 events and had its last release on 2025-11-12; TypeScript has active SDKs. The one replay harness the maintainer found runs the real hook commands and warns you to use an isolated environment. cc-hooks is the typed layer and the dry-run harness for Python: the event list, field names, output shapes and exit-code semantics are generated from the reference as it reads today, and the registry carries the date so you can see when it was last checked.

What it is not: a policy engine, a replacement for the permission system, or a promise about undocumented behaviour. It encodes what the docs say; `docs/events.md` shows every assumption, and [CONTRIBUTING.md](CONTRIBUTING.md) says how to update it when the docs change.

## When to use this

- **How do I write a Claude Code hook in Python without guessing the JSON?** `read_event()` gives you a typed event; `deny("...").exit()` prints the documented shape and exits with the right code.
- **Which of my hooks would fire for this tool call, and in what order?** `cc-hooks explain event.json` merges user, project, local and plugin settings, applies the matchers and prints the handlers with their resolved commands.
- **Can I test my hooks in CI without them touching anything?** `cc-hooks test fixtures/` runs matching command hooks with `CC_HOOKS_DRY_RUN=1` and asserts the decision each fixture expects.
- **How do I get realistic payloads to test against?** `cc-hooks record --install` captures what Claude Code actually sends; `cc-hooks record --to fixtures/` turns the capture into fixtures.
- **What does exit code 2 do on `PostToolUse`?** `cc-hooks events` and [docs/events.md](docs/events.md) answer that for every event (it shows stderr to Claude; the tool already ran).

## 30-second demo

Install the CLI and the library, write a hook, register it, and ask what would happen:

```bash
pipx install git+https://github.com/basitalisandhu/cc-hooks   # the cc-hooks command (see "Install" for the library side)
```

`.claude/hooks/deny_env.py`, ten lines, denies `cat .env` and leaves everything else to the normal permission flow ([examples/minimal.py](examples/minimal.py)):

```python
#!/usr/bin/env python3
"""The README example: deny `cat .env` and let everything else through."""

from cc_hooks import PreToolUse, deny, read_event

event = read_event()
if isinstance(event, PreToolUse) and event.tool_name == "Bash":
    words = (event.command or "").split()
    if "cat" in words and ".env" in words:
        deny("Reading .env prints secrets into the transcript.").exit()
```

`.claude/settings.json` registers it in exec form, so the path placeholder is passed as one argument with no shell:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "python3", "args": ["${CLAUDE_PROJECT_DIR}/.claude/hooks/deny_env.py"]}
        ]
      }
    ]
  }
}
```

Fed the `cat .env` payload, the hook prints the documented `PreToolUse` shape and exits 0:

```text
$ echo '{"session_id":"demo","cwd":"/tmp","hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"cat .env"}}' | python3 examples/minimal.py
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "Reading .env prints secrets into the transcript."}}
```

## Install

Requires Python 3.11 or newer. PyPI publication is pending, so install from the repository until then:

```bash
pipx install git+https://github.com/basitalisandhu/cc-hooks              # the cc-hooks command, isolated
uvx --from git+https://github.com/basitalisandhu/cc-hooks cc-hooks events   # run without installing
python3 -m pip install git+https://github.com/basitalisandhu/cc-hooks    # into the current environment
git clone https://github.com/basitalisandhu/cc-hooks && cd cc-hooks && uv venv && uv pip install -e ".[dev]"   # development
```

Once the package is on PyPI the short forms work too: `pipx install cc-hooks`, `uvx cc-hooks events`, `pip install cc-hooks`.

Two things get installed and they live in different places. The `cc-hooks` command is what pipx or uvx gives you. A hook script that does `from cc_hooks import ...` runs under whatever interpreter your settings name (`python3` in the examples), so that interpreter needs the library: `python3 -m pip install cc-hooks`, or run the hook with `uv run --with cc-hooks hook.py`, or point `command` at a virtualenv's python. There are no runtime dependencies, so a plain `pip install` adds exactly one package.

## Writing a hook

`read_event()` reads stdin, validates `hook_event_name` and the documented required fields, and returns the typed event; unknown fields are kept in `event.extra`, malformed input raises `HookInputError`. Pass `strict=False` for a hook that must keep working when a field disappears in a later Claude Code version.

```python
from cc_hooks import PostToolUse, PreToolUse, Stop, add_context, block, deny, read_event

event = read_event()
match event:
    case PreToolUse(tool_name="Bash") if "rm -rf" in (event.command or ""):
        deny("Use git clean or a targeted rm.").exit()
    case PostToolUse(tool_name="Edit" | "Write") if (event.file_path or "").endswith(".py"):
        add_context("Run ruff before finishing.").exit()
    case Stop(stop_hook_active=False) if not event.last_assistant_message:
        block("Summarise what changed before stopping.", event="Stop").exit()
```

Every event class carries the common fields (`session_id`, `prompt_id`, `transcript_path`, `cwd`, `scratchpad_dir`, `permission_mode`, `effort`, `hook_event_name`, `agent_id`, `agent_type`) plus the event's own fields with the types the docs give them. Tool events add `command`, `file_path` and `is_mcp_tool` helpers, and `event.matcher_value` is the value Claude Code evaluates the `matcher` against (`tool_name`, `source`, `agent_type`, the basename of `file_path` on `FileChanged`, and so on).

### Builders

Each builder returns a `Decision`. `decision.exit()` prints the JSON (or the reason on stderr for an exit-code-2 block) and exits; `decision.emit()` does the same without exiting; `decision.to_dict()` gives you the payload. A builder raises `DecisionError` for a combination the docs do not list, such as `update_output()` on `PreToolUse` or `defer()` on `PreModelSwitch`.

| Builder | Events | What it prints |
|---|---|---|
| `allow(reason, updated_input, additional_context)` | PreToolUse, PreModelSwitch | `hookSpecificOutput.permissionDecision: "allow"`, plus `updatedInput` and `additionalContext` on PreToolUse |
| `deny(reason)` | PreToolUse, PreModelSwitch | `permissionDecision: "deny"` with `permissionDecisionReason` |
| `ask(reason)` | PreToolUse, PreModelSwitch | `permissionDecision: "ask"` |
| `defer()` | PreToolUse | `permissionDecision: "defer"` (non-interactive `-p` runs) |
| `update_input(input, decision="allow")` | PreToolUse | `updatedInput` paired with allow or ask |
| `update_output(value, mcp=False)` | PostToolUse | `updatedToolOutput` (or `updatedMCPToolOutput`) |
| `block(reason)` | the 11 top-level-decision events; TeammateIdle, TaskCompleted, Elicitation, ElicitationResult, WorktreeCreate, WorktreeRemove | `{"decision": "block", "reason": ...}` and exit 0, or the reason on stderr and exit 2 where only the exit code blocks |
| `blocking_error(reason)` | the 16 events where exit 2 blocks | reason on stderr, exit 2 |
| `add_context(text)` | the 11 events that accept `additionalContext` | `hookSpecificOutput.additionalContext` |
| `stop(stop_reason)` | events that honour `continue` | `{"continue": false, "stopReason": ...}` |
| `system_message(text)` | events that show it | `{"systemMessage": ...}` |
| `permission_request(behavior, ...)` | PermissionRequest | `hookSpecificOutput.decision` with `behavior`, `updatedInput`, `updatedPermissions`, `message`, `interrupt` |
| `retry()` | PermissionDenied | `hookSpecificOutput.retry: true` |
| `display(text)` | MessageDisplay | `hookSpecificOutput.displayContent` |
| `elicitation(action, content)` | Elicitation, ElicitationResult | `hookSpecificOutput.action` and `content` |
| `watch_paths(paths)` | SessionStart, CwdChanged, FileChanged | `hookSpecificOutput.watchPaths` |
| `session_start(context, session_title, initial_user_message, watch_paths, reload_skills)` | SessionStart | the five documented SessionStart output fields |
| `no_decision()` | any | prints nothing, exit 0: the normal flow applies |

`decision.with_fields(system_message=..., terminal_sequence=..., continue_=..., stop_reason=...)` adds the universal fields, and refuses them on events whose section says they are discarded.

## The explain, test and record workflow

### `cc-hooks explain`

Give it a payload (a file, or `-` for stdin) and it loads managed, user (`~/.claude/settings.json` or `$CLAUDE_CONFIG_DIR`), project and local settings plus `hooks/hooks.json` of every enabled plugin recorded in `installed_plugins.json`, substitutes `${CLAUDE_PROJECT_DIR}`, `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}`, collapses handlers defined identically in more than one settings file (they run once), applies `disableAllHooks` with settings precedence, resolves the matchers and the `if` rules, and prints what would run. Output for the `cat .env` payload against [examples/settings.json](examples/settings.json):

```text
$ cc-hooks explain event.json --settings examples/settings.json --cwd .
cc-hooks explain: PreToolUse  (tool_name='Bash')
project root: /home/user/cc-hooks
sources:
  project                  /home/user/cc-hooks/examples/settings.json
handlers that would run: 1 of 1 PreToolUse handler(s); Claude Code runs them in parallel
  1. [project] exact matcher 'Bash|PowerShell'; command, timeout 10s
     python3 /home/user/cc-hooks/examples/deny_secrets.py
decision control: hookSpecificOutput
  fields: permissionDecision, permissionDecisionReason, updatedInput, additionalContext
  precedence across hooks: deny > defer > ask > allow
exit code 2: blocks. Blocks the tool call
docs: https://code.claude.com/docs/en/hooks#pretooluse
```

Without `--settings` it discovers every source for `--cwd` (or, failing that, for the payload's `cwd`) and lists each one, marking the files it did not find. Handlers that exist for the event but did not match are listed under `not matched` with their matcher, so a `mcp__memory` matcher that never fires (the docs: without `.*` it is an exact string) is visible. `--json` gives the same information as a structure.

Matchers follow the reference's table: `*`, empty or omitted matches everything; a value made only of letters, digits, `_`, `-`, spaces, `,` and `|` is an exact string or a `|` or `,` separated list; anything else is an unanchored regular expression (`FileChanged` and `StopFailure` use the narrower set, where a hyphen keeps the matcher on the regex path). The `if` field is evaluated best-effort the way the docs' Bash table describes, including `$()` and backticks and leading `VAR=value` assignments; when it cannot be evaluated the handler is listed as running, which is what Claude Code does.

### `cc-hooks test`

A fixture is a JSON file with the payload and what your hooks should make of it:

```json
{
  "event": {"session_id": "abc123", "cwd": "/home/user/demo", "hook_event_name": "PreToolUse",
            "tool_name": "Bash", "tool_input": {"command": "cat .env"}},
  "expect": {"decision": "deny", "reason_contains": "secrets", "handlers": 1}
}
```

`expect.decision` is one of `allow`, `deny`, `ask`, `defer`, `block`, `stop`, `context`, `update_output`, `retry`, `display`, `accept`, `decline`, `cancel`, `error` (a non-blocking error, for example exit 1 with no JSON) or `none` (no handler decided). `reason_contains`, `context_contains`, `handlers` (how many matched) and `exit_code` are optional. The runner resolves the handlers exactly as `explain` does, runs each matching `command` handler with the payload on stdin and `CC_HOOKS_DRY_RUN=1` in the environment, applies the documented rules to its stdout, stderr and exit code (exit 2 blocks where the table says it blocks; valid JSON is honoured on any exit code; plain text is context only on the four events where the docs say so), combines several hooks with the documented precedence, and compares. The repository's own examples:

```text
$ cc-hooks test examples/fixtures --settings examples/settings.json --cwd .
cc-hooks test: 5 fixture(s), dry run (CC_HOOKS_DRY_RUN=1)
project root: /home/user/cc-hooks
settings: /home/user/cc-hooks/examples/settings.json
PASS  allow-ls.json          PreToolUse         handlers=1  decision=none
      1. [project] python3 /home/user/cc-hooks/examples/deny_secrets.py: exit 0, none
PASS  deny-cat-env.json      PreToolUse         handlers=1  decision=deny  reason="Reading a .env file prints secrets into the t..."
      1. [project] python3 /home/user/cc-hooks/examples/deny_secrets.py: exit 0, deny "Reading a .env file prints secrets into the t..."
PASS  edit-not-matched.json  PreToolUse         handlers=0  decision=none
PASS  post-tool-use.json     PostToolUse        handlers=1  decision=none
      1. [project] python3 /home/user/cc-hooks/examples/log_tool_use.py: exit 0, none
PASS  session-start.json     SessionStart       handlers=1  decision=context
      1. [project] python3 /home/user/cc-hooks/examples/session_context.py: exit 0, context
summary: 5 passed, 0 failed
```

The exit status is 1 when any fixture fails, so the command belongs in CI (this repository's workflow runs it). `--no-run` prints the resolved commands without executing anything; `--exec` removes `CC_HOOKS_DRY_RUN` for hooks whose side effects you want; `--report results.json` writes the table as JSON; `--timeout` caps each hook (30 seconds by default, under the handler's own timeout).

Dry run is a contract between the runner and your hooks, not a sandbox: a hook that ignores the variable runs for real. Hooks written with this library can check `cc_hooks.dry_run()` and skip the side effect, as [examples/log_tool_use.py](examples/log_tool_use.py) does. `http`, `mcp_tool`, `prompt` and `agent` handlers are never executed; they are listed as "would call".

### `cc-hooks record`

```text
$ cc-hooks record --install
installed record handler on PreToolUse, PostToolUse in .claude/settings.local.json (2 added)
payloads will be appended to ~/.cc-hooks/recorded.jsonl
convert them with: cc-hooks record --to fixtures/
```

`--install` adds one exec-form handler with no matcher to `PreToolUse` and `PostToolUse` (change the list with `--events`, the file with `--scope user|project|local` or `--file`), idempotently, without touching anything else in the settings file. The handler is `cc-hooks record --append FILE`: it appends `{"recorded_at": ..., "event": <payload>}` as one line and always exits 0, so recording never changes what Claude does. Work for a while, then `cc-hooks record --to fixtures/` writes one fixture per payload with `expect.decision` set to `none` for you to edit, and `cc-hooks record --uninstall` removes the handlers. The recorded file holds command lines and file contents; keep it out of version control.

## Events

`cc-hooks events` prints the registry; [docs/events.md](docs/events.md) is the same data with every field, the `hookSpecificOutput` fields, the exit-code-2 row and the example payload from the reference for each of the 33 events, plus the `tool_input` fields of the built-in tools. The docs were read on 2026-10-03; `cc_hooks.DOCS_FETCHED` carries the date.

```text
$ cc-hooks events
33 hook events, from https://code.claude.com/docs/en/hooks (2026-10-03)

 #  Event                Matcher on                Exit 2   Decision
--------------------------------------------------------------------
 1  SessionStart         source                    no       Context only (hookSpecificOutput)
 2  Setup                trigger                   no       None; JSON output is discarded
 3  InstructionsLoaded   load_reason               no       None
 4  UserPromptSubmit     (none)                    blocks   Top-level decision
 5  UserPromptExpansion  command_name              blocks   Top-level decision
 6  MessageDisplay       (none)                    no       hookSpecificOutput.displayContent (display only)
 7  PreToolUse           tool_name                 blocks   hookSpecificOutput
 8  PermissionRequest    tool_name                 no       hookSpecificOutput.decision
 9  PostToolUse          tool_name                 no       Top-level decision plus hookSpecificOutput
10  PostToolUseFailure   tool_name                 no       Top-level decision plus hookSpecificOutput
11  PostToolBatch        (none)                    blocks   Top-level decision
12  PermissionDenied     tool_name                 no       hookSpecificOutput.retry
13  Notification         notification_type         no       None
14  SubagentStart        agent_type                no       Context only (hookSpecificOutput)
15  SubagentStop         agent_type                blocks   Top-level decision plus hookSpecificOutput.additionalContext
16  TaskCreated          (none)                    blocks   Exit code or top-level decision
17  TaskCompleted        (none)                    blocks   Exit code or continue: false
18  Stop                 (none)                    blocks   Top-level decision plus hookSpecificOutput.additionalContext
19  StopFailure          error                     no       None; output and exit code are ignored except terminalSequence
20  TeammateIdle         (none)                    blocks   Exit code or continue: false
21  ConfigChange         source                    blocks   Top-level decision
22  CwdChanged           (none)                    no       None; watchPaths output only
23  DirectoryAdded       source                    no       None
24  FileChanged          basename(file_path)       no       None; watchPaths output only
25  WorktreeCreate       (none)                    blocks   Path return: command hooks print the path, HTTP hooks return worktreePath
26  WorktreeRemove       (none)                    blocks   Exit code only; JSON output is discarded
27  PreCompact           trigger                   blocks   Top-level decision
28  PostCompact          trigger                   no       None
29  PreModelSwitch       to_model                  blocks   hookSpecificOutput or top-level decision
30  PostModelSwitch      to_model                  no       Context only (hookSpecificOutput)
31  SessionEnd           reason                    no       None; JSON output is discarded
32  Elicitation          mcp_server_name           blocks   hookSpecificOutput
33  ElicitationResult    mcp_server_name           blocks   hookSpecificOutput
```

## Examples

- [examples/deny_secrets.py](examples/deny_secrets.py): `PreToolUse` on `Bash|PowerShell`, denies reading dotenv files with a pager or cat-like program, bare environment dumps, and `echo $SOME_TOKEN`.
- [examples/session_context.py](examples/session_context.py): `SessionStart`, adds the branch and uncommitted files as `additionalContext` and names the session when it has no custom title.
- [examples/log_tool_use.py](examples/log_tool_use.py): `PostToolUse` on every tool, appends one JSON line per call and does nothing under `cc-hooks test`.
- [examples/settings.json](examples/settings.json) registers all three; [examples/fixtures/](examples/fixtures/) are the fixtures shown above.

## Limits

- Hooks declared in skill or subagent frontmatter exist only while those components are active in a session; `explain` and `test` do not see them.
- Regular-expression matchers are evaluated with Python's `re`, not JavaScript's; the two agree on the patterns in the docs but not on every construct.
- `if` rule evaluation is best effort, as the docs say Claude Code's own is. When in doubt the handler is listed as running.
- `explain` cannot know the directory a session was started in. Pass it with `--cwd`; otherwise the nearest `.claude/` or `.git` above the payload's `cwd` is used.
- The registry reflects the docs on the date in `DOCS_FETCHED`. Claude Code can change between versions; `cc-hooks record` shows you what your version actually sends.

## Frequently asked questions

**Does `cc-hooks test` run my hooks for real?**
It runs the matching `command` handlers as real subprocesses with the fixture payload on stdin, because the decision comes from the hook's own stdout and exit code; there is no other way to know what the hook would say. It sets `CC_HOOKS_DRY_RUN=1` so a hook written with this library can skip side effects (`cc_hooks.dry_run()`), it never executes `http`, `mcp_tool`, `prompt` or `agent` handlers, and `--no-run` only prints the resolved commands. A hook that writes files regardless of the variable will write them. Nothing is sent anywhere.

**The brief I read says 32 events and another SDK covers 9. Which number is right?**
The hooks reference listed 33 events when this registry was generated on 2026-10-03, and `tests/test_events.py` pins that list in the order the reference uses. When the docs change, the update is a data edit in `src/cc_hooks/events.py` followed by `make docs`; [CONTRIBUTING.md](CONTRIBUTING.md) walks through it. The count in this README is checked by a test against the registry, so it cannot drift silently.

**I defined the same hook in my user settings and in the project settings. Why does `explain` list it once?**
Because that is what Claude Code does: hook entries merge across settings levels, and a handler defined identically in more than one settings file runs once. A plugin's copy of the same handler stays separate and is listed under its plugin name. The order printed is the source order (managed, user, project, local, plugins); Claude Code runs all matching handlers in parallel and combines the results with the precedence shown under "decision control".

**How do I target an MCP tool?**
Tool events carry the tool as `mcp__<server>__<tool>` (plugin-bundled servers use `mcp__plugin_<plugin>_<server>__<tool>`), so the matcher is `mcp__memory__.*` for one server or `mcp__.*__write.*` for a verb across servers; the `.*` is required, since a matcher without it is an exact string. In the hook, `event.is_mcp_tool` is true and `event.mcp_server` holds the server's `name` and `source` on Claude Code v2.1.274 or later. `explain` shows whether your matcher landed on the exact or the regex path.

## Contributing

Issues and pull requests are welcome; the most useful ones are docs drift reports with the payload that shows it. Run `make check` (ruff and pytest) before opening a pull request, and see [CONTRIBUTING.md](CONTRIBUTING.md) for how the event registry is updated. [docs/good-first-issues.md](docs/good-first-issues.md) lists five scoped starting points. Security problems: see [SECURITY.md](SECURITY.md).

## Sibling projects

- [masoon](https://github.com/basitalisandhu/masoon): open-source trust infrastructure for AI agents: who they are, what they may touch, and proof of what they did.
- [agent-security-skills](https://github.com/basitalisandhu/agent-security-skills): Claude Code plugin and skill pack for agent security reviews, with guard hooks for `Bash` written as plain scripts of the kind this library replaces.
- [claude-dev-skills](https://github.com/basitalisandhu/claude-dev-skills): Claude Code skills from the same maintainer.

## Licence

MIT, see [LICENSE](LICENSE). Copyright 2026 Muhammad Basit Ali.
