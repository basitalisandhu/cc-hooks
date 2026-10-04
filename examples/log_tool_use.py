#!/usr/bin/env python3
"""PostToolUse hook: append one JSON line per tool call to a log file.

Register it under PostToolUse with no matcher (every tool, every subagent); see
examples/settings.json. The log goes to $CC_HOOKS_TOOL_LOG or ~/.cc-hooks/tool-use.jsonl.

It writes nothing when `cc-hooks test` replays it (dry_run() is true), prints nothing on
stdout, and always exits 0: a logging hook must never change what Claude does.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

from cc_hooks import HookInputError, PostToolUse, dry_run, read_event

DEFAULT_LOG = Path("~/.cc-hooks/tool-use.jsonl")


def main() -> int:
    try:
        event = read_event()
    except HookInputError as exc:
        print(f"log_tool_use: {exc}", file=sys.stderr)
        return 0
    if not isinstance(event, PostToolUse):
        return 0
    record = {
        "time": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "session_id": event.session_id,
        "agent_id": event.agent_id,
        "tool_name": event.tool_name,
        "tool_use_id": event.tool_use_id,
        "duration_ms": event.duration_ms,
        "cwd": event.cwd,
        "file_path": event.file_path,
        "command": event.command,
    }
    if dry_run():
        print(f"log_tool_use: dry run, would log {event.tool_name}", file=sys.stderr)
        return 0
    path = Path(os.environ.get("CC_HOOKS_TOOL_LOG") or DEFAULT_LOG).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        print(f"log_tool_use: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
