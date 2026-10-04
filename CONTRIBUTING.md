# Contributing

Thanks for considering a contribution. The project is small on purpose: an event registry, decision builders, a settings resolver and a CLI. The most useful contributions are keeping the registry in step with the Claude Code docs, fixtures from real sessions that show an edge case, and better `if` rule evaluation.

## Set up

Requires Python 3.11 or newer. With [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/basitalisandhu/cc-hooks
cd cc-hooks
uv venv && uv pip install -e ".[dev]"
uv run pytest -q
```

Without uv:

```bash
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -e ".[dev]"
python3 -m pytest -q
```

## Before you open a pull request

```bash
make check          # ruff check, ruff format --check, pytest
make docs           # regenerate docs/events.md from the registry
make examples       # replay examples/fixtures through examples/settings.json
```

CI runs the same commands on Python 3.11 and 3.12 and fails if `docs/events.md` is out of date.

## Updating the event registry when the docs change

The hooks reference at <https://code.claude.com/docs/en/hooks> is the single source. When it changes:

1. Edit the matching `EventSpec` in `src/cc_hooks/events.py` (or add one, in the order the reference lists events): fields with their type and whether the docs say the field is always present, the matcher field, the decision pattern, the output fields and the exit code 2 row from the per-event table.
2. Copy the example payload from the event's section. Replace any model identifier with a placeholder such as `<model-id>`; keep every other value as printed.
3. Update the matching dataclass so its fields equal the common fields plus the spec's fields; `tests/test_events.py` fails otherwise. A field that is required in the spec must have no default on the class. If the field is one of the common ones (`agent_id`, `agent_type`), redeclare it with `field()` so it does not inherit the base default.
4. If the event's decision shape changed, update the event sets at the top of `src/cc_hooks/decide.py` and the builders that use them, and add a round-trip row in `tests/test_decide.py`.
5. Bump `DOCS_FETCHED` and `SCHEMA_VERSION`, run `make docs`, and note the change in `CHANGELOG.md` with the doc section it came from.

Do not add behaviour the docs do not describe. If you observed Claude Code doing something the docs do not say, record the payload with `cc-hooks record`, add it as a fixture and link the docs page in the pull request so the difference is visible.

## Style

- `ruff` formats and lints; line length 100.
- Standard library only. A pull request that adds a runtime dependency will be asked to remove it.
- No model names or vendor identifiers in code, docs or fixtures; Claude Code as the host product is fine.
- Plain language, no em dashes, no claims that cannot be checked against the docs or a test.
- Deterministic output: the CLI prints nothing that depends on the time of day except `record` timestamps.

## Reporting security issues

See [SECURITY.md](SECURITY.md).
