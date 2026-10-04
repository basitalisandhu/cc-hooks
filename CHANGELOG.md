# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/basitalisandhu/cc-hooks/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/basitalisandhu/cc-hooks/releases/tag/v0.1.0
