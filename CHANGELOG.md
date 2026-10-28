# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

Nothing yet.

## [0.2.0] - 2026-10-28

### Added

- `cc-hooks test` and `cc-hooks explain` show what an `http` handler would send: `POST <url> with headers Name: <redacted>`, with header values and any password in the URL redacted; `test` lists these handlers as "would call" instead of "would execute" (part of #5).
- A fixture's `expect` is validated before anything runs: an unknown `decision`, a non-numeric `handlers` or `exit_code`, or a non-string `reason_contains` or `context_contains` is reported as an error for that file.
- README: a Troubleshooting section, and a `quickstart` CI job (`scripts/check_readme_quickstart.py`) that follows the 30-second demo on a fresh pipx and pip install and checks the output it shows.
- Property tests with seeded random payloads, settings and fixtures, and scaling tests on ten times the input.
- Recorded-fixture conversion deduplicates event/tool/input identities by default, supports repeatable `--event`/`--tool` filters and timezone-aware `--since`, preserves sparse fixture numbering and offers `--keep-duplicates` (#8).

### Fixed

- A payload, settings, fixture or recording file that is not UTF-8, or is JSON nested too deeply, gives one error line naming the file instead of a traceback; `read_event()` raises `HookInputError` for deep nesting.
- A settings entry whose `prompt`, `url`, `server` or `tool` is a JSON object or array no longer crashes de-duplication.
- `cc-hooks record --install` no longer crashes on a settings group whose `hooks` is not a list, and `--uninstall` no longer creates a settings file that did not exist.
- An output path in a missing directory (`--report`, `--file`) ends with `cc-hooks: cannot read or write <path>` and exit 2; any other unexpected error prints one line asking for a report.

### Performance

- Stripping leading `VAR=value` assignments from a Bash command for `if` rules was quadratic in their number; it is one regular-expression match now, and the parsed subcommands are cached across handlers.

## [0.1.2] - 2026-10-16

### Changed

- `cc-hooks test` now shows the exception line of a hook's Python traceback instead of its first line, and when the hook cannot import `cc_hooks` it says how to install the library for the interpreter that runs it.
- `cc-hooks record --help` describes `--scope`, `--cwd` and `--home`.
- The README demo installs the library as well as the command, and says what happens when a hook's interpreter lacks it.
- CI and release workflows use actions/checkout 7, actions/setup-python 7, actions/upload-artifact 7 and actions/download-artifact 8.

## [0.1.1] - 2026-10-06

### Changed

- Removed the umbrella branding; this project stands alone and links its sibling repositories directly.

### Fixed

- Hook paths written with Windows separators now match across platforms (#10, thanks @nawaaaaaAaar).

## [0.1.0] - 2026-10-04

### Added

- Container image `ghcr.io/basitalisandhu/cc-hooks` for linux/amd64 and linux/arm64, published on each version tag with an SPDX SBOM, a build provenance attestation and a keyless cosign signature. The image runs as uid 1000 with `/work` as the working directory.
- `cc_hooks.events`: one frozen dataclass per hook event in the Claude Code hooks reference as read on 2026-10-03 (33 events), `read_event()` and `parse_event()` with strict and lenient modes, `HookInputError`, the `EVENTS` registry with documented fields, matcher fields, decision patterns, output fields, exit code 2 behaviour and example payloads, and `TOOL_INPUT_FIELDS` for the built-in tools.
- `cc_hooks.decide`: `allow`, `deny`, `ask`, `defer`, `update_input`, `update_output`, `block`, `blocking_error`, `add_context`, `stop`, `system_message`, `permission_request`, `retry`, `display`, `elicitation`, `watch_paths`, `session_start` and `no_decision`, each printing the documented JSON shape and exit code and refusing combinations the docs do not list; `observe()` and `combine()` apply the documented exit-code rules and precedence to hook output.
- `cc_hooks.settings`: discovery and merge of managed, user, project, local and enabled-plugin hook settings, `${CLAUDE_PROJECT_DIR}`, `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` substitution, duplicate handler collapse, `disableAllHooks` precedence, matcher resolution (all, exact lists, regular expressions, the narrower FileChanged and StopFailure set) and best-effort `if` rule evaluation.
- `cc-hooks events`, `cc-hooks explain`, `cc-hooks test` (dry run by default, `--exec`, `--no-run`, `--settings`, `--report`) and `cc-hooks record` (`--install`, `--uninstall`, `--append`, `--to`).
- Three example hooks with fixtures, generated `docs/events.md`, CI on Python 3.11 and 3.12, PyPI trusted publishing on tags (off until the repository variable `PYPI_PUBLISH` is set).

### Changed

- Renamed the umbrella project from Hisar to Masoon; links, names and identifiers updated.

[Unreleased]: https://github.com/basitalisandhu/cc-hooks/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/basitalisandhu/cc-hooks/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/basitalisandhu/cc-hooks/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/basitalisandhu/cc-hooks/releases/tag/v0.1.0
