# Good first issues

Issues the maintainer intends to open under the `good first issue` label, written out so
they can be filed in one sitting. Each is self-contained and has acceptance criteria that
`make check` can verify. Read [CONTRIBUTING.md](../CONTRIBUTING.md) first: `ruff` must pass,
`docs/events.md` is generated (`make docs`), the package stays standard-library only, and
nothing may be added that the hooks reference at <https://code.claude.com/docs/en/hooks>
does not describe.

## 1. Build the payload for `cc-hooks explain` from flags

**Context.** `cc-hooks explain` takes a JSON file. Checking "which hooks fire when Claude
runs `git push`?" means writing a file first.

**Acceptance criteria.**

- `cc-hooks explain --event PreToolUse --tool Bash --command "git push"` works without a
  file; `--file-path` fills `tool_input.file_path` for the file tools, and `--value` sets
  the matcher value for events whose matcher is not the tool name (for example
  `--event SessionStart --value resume`).
- The synthesized payload carries `session_id` `"explain"`, `cwd` set to `--cwd` or the
  current directory, and the event's required fields; it must parse in strict mode.
- A positional file and the flags are mutually exclusive, with a clear error otherwise.
- Tests in `tests/test_cli.py` cover a tool event, a matcher-value event and the error.
- README "explain" section shows the flag form once.

## 2. Show what an `http` handler would send

**Context.** `cc-hooks test` never executes `http`, `mcp_tool`, `prompt` or `agent`
handlers and prints "would call". For `http` hooks the useful information is the request
that would go out.

**Acceptance criteria.**

- In `cc-hooks test` and `cc-hooks explain`, an `http` handler line shows the URL and the
  header names from the handler's `headers` field with values replaced by `<redacted>`.
- `cc-hooks test --exec` still does not send the request; add `--exec-http` that POSTs
  the event JSON with `urllib.request`, applies the documented response rules (2xx with an
  empty body is success, 2xx with a JSON object is parsed, anything else is a non-blocking
  error) through `observe()`, and honours the handler timeout.
- Tests use `http.server` in a thread as the endpoint; no network beyond localhost.

## 3. Validate a hook's output against the event's documented fields

**Context.** `observe()` reads the decision out of a hook's stdout but does not say when
the JSON carries a field the event ignores, such as `additionalContext` on `Notification`
or `updatedInput` on `PostToolUse`. Claude Code reports schema failures as non-blocking
errors, so a typo silently disables a policy hook.

**Acceptance criteria.**

- New function `cc_hooks.decide.validate_output(event, payload) -> list[str]` returns one
  message per unknown `hookSpecificOutput` key, per `hookEventName` mismatch, and per
  top-level `decision` value other than `"block"` on the events that use it.
- `cc-hooks test` prints those messages under the handler line and counts the fixture
  as failed when `expect.strict_output` is true.
- `cc-hooks validate FILE` reads a JSON output file (or stdin) with `--event NAME` and
  exits 1 on any message.
- Tests cover a valid output for every event in the registry (use the builders) and at
  least three invalid ones.

## 4. Windows path handling in matchers and `if` rules

**Context.** The reference says `tool_input.file_path` arrives with backslashes on
Windows and that forward-slash comparisons never match. `if_matches()` normalizes
separators, but there are no tests for it, and `FileChanged`'s basename matcher uses
`os.path.basename`, which does not split on backslashes on Linux.

**Acceptance criteria.**

- `Event.matcher_value` for `FileChanged` returns the basename for both separator styles
  on every platform (use `ntpath` when the path contains a backslash and no slash).
- `if_matches("Edit(src/**)", ...)` and `if_matches("Edit(*.ts)", ...)` are tested with
  `C:\project\src\index.ts` and `cwd` `C:\project`.
- `tests/test_settings.py` gains a parametrized Windows block; nothing else changes
  behaviour on POSIX paths.

## 5. Make `cc-hooks record --to` deduplicate and filter

**Context.** A busy session records hundreds of near-identical `PostToolUse` payloads.
Converting them one-to-one produces a noisy fixture directory.

**Acceptance criteria.**

- `--to` skips a payload whose `hook_event_name`, `tool_name` and `tool_input` equal an
  earlier one (ignore `tool_response`, ids, paths and timestamps) unless `--keep-duplicates`
  is passed, and reports how many it skipped.
- `--event NAME` (repeatable) and `--tool NAME` (repeatable) keep only matching payloads;
  `--since 2026-10-01T00:00:00Z` keeps payloads recorded at or after that time.
- File names stay `NNN-<event>-<tool>.json` and numbering continues from the files already
  in the directory.
- Tests in `tests/test_cli.py` cover duplicates, each filter and the combination.
