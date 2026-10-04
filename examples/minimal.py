#!/usr/bin/env python3
"""The README example: deny `cat .env` and let everything else through."""

from cc_hooks import PreToolUse, deny, read_event

event = read_event()
if isinstance(event, PreToolUse) and event.tool_name == "Bash":
    words = (event.command or "").split()
    if "cat" in words and ".env" in words:
        deny("Reading .env prints secrets into the transcript.").exit()
