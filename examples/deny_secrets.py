#!/usr/bin/env python3
"""PreToolUse hook: deny shell commands that would print secrets into the transcript.

Register it under PreToolUse with matcher "Bash" (or "Bash|PowerShell"); see
examples/settings.json. The hook denies when a command reads a dotenv file with a
pager or cat-like program, dumps the whole environment, or echoes a variable whose
name looks like a credential. Everything else gets no decision, so the normal
permission flow applies.

Exit code 0 with the JSON decision is what Claude Code expects; the reason is shown
to Claude so it can pick another approach.
"""

from __future__ import annotations

import re
import sys

from cc_hooks import HookInputError, PreToolUse, deny, no_decision, read_event

DOTENV = re.compile(r"(^|[\s/\"'=])\.env(\.[\w.-]+)?([\s\"']|$)")
READERS = {"cat", "less", "more", "head", "tail", "bat", "strings", "base64", "xxd", "hexdump"}
DUMPERS = {"printenv", "env", "set", "export"}
SECRET_VAR = re.compile(
    r"\$\{?[A-Z0-9_]*(SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY)[A-Z0-9_]*\}?"
)


def reason_to_deny(command: str) -> str | None:
    words = set(re.split(r"[\s;&|()]+", command))
    if DOTENV.search(command) and words & READERS:
        return "Reading a .env file prints secrets into the transcript."
    for segment in re.split(r"&&|\|\||;|\|", command):
        tokens = segment.split()
        if len(tokens) == 1 and tokens[0] in DUMPERS:
            return "Dumping the whole environment prints secrets into the transcript."
        if tokens and tokens[0] == "echo" and SECRET_VAR.search(segment):
            return "Echoing a credential variable prints its value into the transcript."
    return None


def main() -> int:
    try:
        event = read_event()
    except HookInputError as exc:
        print(f"deny_secrets: {exc}", file=sys.stderr)
        return 1  # a non-blocking error; the tool call proceeds
    if not isinstance(event, PreToolUse) or event.tool_name not in ("Bash", "PowerShell"):
        return no_decision().emit()
    reason = reason_to_deny(event.command or "")
    if reason is None:
        return no_decision().emit()
    hint = 'To check that a variable is set, use [ -n "$NAME" ] && echo set.'
    return deny(f"{reason} {hint}").emit()


if __name__ == "__main__":
    sys.exit(main())
