"""Generated and hand-written documents stay in step with the code."""

from __future__ import annotations

import re

from cc_hooks import EVENTS, __version__
from cc_hooks.cli import render_events_markdown

from .conftest import ROOT


def test_events_doc_is_current():
    assert (ROOT / "docs" / "events.md").read_text(encoding="utf-8") == render_events_markdown()


def test_readme_states_the_documented_event_count():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"{len(EVENTS)} " in readme
    assert "https://code.claude.com/docs/en/hooks" in readme
    for name in ("explain", "test", "record"):
        assert f"cc-hooks {name}" in readme


def test_no_em_dashes_in_prose():
    for path in list(ROOT.glob("*.md")) + list((ROOT / "docs").glob("*.md")):
        assert "—" not in path.read_text(encoding="utf-8"), path


def test_changelog_mentions_the_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## \[{re.escape(__version__)}\]", changelog, re.M)


def test_pyproject_version_matches_package():
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == __version__
    assert data["project"]["dependencies"] == []
