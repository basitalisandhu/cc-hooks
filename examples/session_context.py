#!/usr/bin/env python3
"""SessionStart hook: tell Claude which branch it is on and what is uncommitted.

Register it under SessionStart with no matcher (every start, resume, clear, compact
and fork); see examples/settings.json. The output uses the documented SessionStart
object: additionalContext for the facts, and sessionTitle only when the session has no
custom title yet (the input carries session_title when one is set).

The hook only reads git state, so it is safe to replay with `cc-hooks test`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from cc_hooks import HookInputError, SessionStart, no_decision, read_event, session_start


def git(args: list[str], cwd: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def main() -> int:
    try:
        event = read_event()
    except HookInputError as exc:
        print(f"session_context: {exc}", file=sys.stderr)
        return 1
    if not isinstance(event, SessionStart):
        return no_decision().emit()
    cwd = event.cwd if event.cwd and Path(event.cwd).is_dir() else "."
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if branch is None:
        return no_decision().emit()  # not a git checkout: nothing worth saying
    status = git(["status", "--porcelain"], cwd) or ""
    changed = [line[3:] for line in status.splitlines() if line.strip()]
    facts = [f"Current branch: {branch}", f"Session source: {event.source}"]
    if changed:
        facts.append("Uncommitted changes: " + ", ".join(changed[:10]))
    else:
        facts.append("Working tree clean")
    title = None if event.session_title else Path(cwd).resolve().name + "@" + branch
    return session_start("\n".join(facts), session_title=title).emit()


if __name__ == "__main__":
    sys.exit(main())
