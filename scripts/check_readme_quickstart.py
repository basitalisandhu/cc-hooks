#!/usr/bin/env python3
"""Follow the README's "30-second demo" on a fresh install and check it does what it says.

Reads the demo section of README.md and, in a new temporary directory:

1. runs the two install commands from the bash block, with the repository URL replaced by this
   checkout (pipx into a private PIPX_HOME, pip into a new virtual environment whose python3 is
   first on PATH, so nothing outside the temporary directory is touched);
2. writes the Python block to .claude/hooks/deny_env.py and the JSON block to .claude/settings.json
   of a new project;
3. runs the command in the text block and compares its output with the README;
4. asks the installed cc-hooks to explain and test the project, which needs both installs: the
   command from pipx, the library for the python3 that runs the hook.

Exit 0 when every step matches, 1 with the difference otherwise. Needs network access for pip to
fetch the build backend. Standard library only.

Usage: python3 scripts/check_readme_quickstart.py [--keep]
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "git+https://github.com/basitalisandhu/cc-hooks"


def demo_blocks(readme: str) -> dict[str, list[str]]:
    section = readme.split("## 30-second demo", 1)[1].split("\n## ", 1)[0]
    blocks: dict[str, list[str]] = {}
    for lang, body in re.findall(r"```(\w+)\n(.*?)```", section, re.S):
        blocks.setdefault(lang, []).append(body)
    for lang in ("bash", "python", "json", "text"):
        if lang not in blocks:
            raise SystemExit(f"README.md: the 30-second demo has no ```{lang} block")
    return blocks


def sh(command: str, *, cwd: Path, env: dict[str, str], stdin: str | None = None) -> str:
    proc = subprocess.run(
        command, shell=True, cwd=cwd, env=env, input=stdin, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise SystemExit(f"$ {command}\nexit {proc.returncode}\n{proc.stdout}{proc.stderr}")
    return proc.stdout


def main() -> int:
    keep = "--keep" in sys.argv[1:]
    blocks = demo_blocks((ROOT / "README.md").read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="cc-hooks-quickstart-"))
    try:
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("CLAUDE_CONFIG_DIR", None)
        (work / "home").mkdir()
        venv.create(work / "venv", with_pip=True)
        env.update(
            PIPX_HOME=str(work / "pipx"),
            PIPX_BIN_DIR=str(work / "bin"),
            PATH=os.pathsep.join([str(work / "bin"), str(work / "venv" / "bin"), env["PATH"]]),
            PIP_DISABLE_PIP_VERSION_CHECK="1",
            HOME=str(work / "home"),  # no user-level hooks from the machine running this
        )

        # 1. The install commands, pointed at this checkout.
        installs = [line.split("#", 1)[0].strip() for line in blocks["bash"][0].splitlines()]
        installs = [line for line in installs if line]
        if not any(c.startswith("pipx install") for c in installs) or not any(
            "pip install" in c for c in installs
        ):
            raise SystemExit(f"README demo install block changed shape: {installs}")
        for command in installs:
            print(f"$ {command}")
            sh(command.replace(REPO_URL, str(ROOT)), cwd=work, env=env)

        # 2. The hook and the settings that register it.
        project = work / "project"
        (project / ".claude" / "hooks").mkdir(parents=True)
        (project / ".git").mkdir()  # the demo runs inside a repository
        hook = project / ".claude" / "hooks" / "deny_env.py"
        hook.write_text(blocks["python"][0], encoding="utf-8")
        settings = json.loads(blocks["json"][0])
        (project / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

        # 3. The command shown in the text block, and the output it promises.
        first, *expected = blocks["text"][0].rstrip("\n").splitlines()
        command = first.removeprefix("$ ")
        print(f"$ {command}")
        got = sh(command, cwd=ROOT, env=env).rstrip("\n").splitlines()
        if got != expected:
            print(f"output differs from the README:\n  README: {expected}\n  got:    {got}")
            return 1
        payload = command.split("'")[1]

        # 4. explain and test through the pipx-installed command, from inside the project.
        (project / "event.json").write_text(payload, encoding="utf-8")
        print("$ cc-hooks explain event.json --cwd .")
        out = sh("cc-hooks explain event.json --cwd .", cwd=project, env=env)
        if "handlers that would run: 1 of 1" not in out or "deny_env.py" not in out:
            print(f"cc-hooks explain did not list the README hook:\n{out}")
            return 1
        fixture = {"event": json.loads(payload), "expect": {"decision": "deny", "handlers": 1}}
        (project / "deny.json").write_text(json.dumps(fixture), encoding="utf-8")
        print("$ cc-hooks test deny.json --cwd .")
        out = sh("cc-hooks test deny.json --cwd .", cwd=project, env=env)
        if "summary: 1 passed, 0 failed" not in out:
            print(f"cc-hooks test did not pass the README hook:\n{out}")
            return 1
        print("README quick start: every step matches")
        return 0
    finally:
        if keep:
            print(f"kept {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
