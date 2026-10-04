# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | yes |

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting on this repository (Security tab, "Report a vulnerability") rather than a public issue. Include the version, a minimal settings file or payload that reproduces the problem and what you expected to happen.

You will get an acknowledgement within 7 days and a fix or a mitigation plan within 30 days for confirmed issues. Credit is given in the release notes unless you prefer otherwise.

## Scope

cc-hooks reads JSON from stdin and from settings files, and prints JSON. It makes no network calls and has no dependencies outside the Python standard library.

Two commands execute other programs, and only the programs your own settings name:

- `cc-hooks test` runs the `command` handlers that match each fixture. By default it sets `CC_HOOKS_DRY_RUN=1` so hooks written with this library can skip side effects, but a hook that ignores the variable runs for real. `--no-run` prints the commands without executing anything; `--exec` removes the variable. `http`, `mcp_tool`, `prompt` and `agent` handlers are never executed.
- `cc-hooks record --install` edits a settings file to add a handler that appends raw hook payloads, which can include file contents and command lines, to `~/.cc-hooks/recorded.jsonl`. Treat that file as sensitive and run `cc-hooks record --uninstall` when you are done.

Issues of interest include a settings file or payload that makes `explain` or `test` execute something it should not, placeholder substitution that lets a path escape the plugin or project directory, a matcher or `if` evaluation that disagrees with the documented rules in a way that hides a handler, and anything that lets a hook's output be mis-read as a different decision.

Hooks you write with the library are your own code. A hook that denies the wrong command is a bug to report here only if the library produced the wrong JSON or exit code for what the hook asked for.
