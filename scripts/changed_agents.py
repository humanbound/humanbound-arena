# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
"""Print, as a JSON list, the ids of agents changed since a git ref.

    python scripts/changed_agents.py --base origin/main

Every agent counts as changed when the tooling that builds and tests agents (scripts/, tools/)
changed, or when the base is unknown (a repository's first push, a force-push).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import arena_lib

TOOLING_DIRS = ("scripts/", "tools/")


def changed_ids(paths: list[str], root: Path) -> list[str]:
    existing = {a.rel_path: a.id for a in arena_lib.discover(root)}
    if any(p.startswith(TOOLING_DIRS) for p in paths):
        return sorted(existing.values())
    ids = set()
    for path in paths:
        parts = path.split("/")
        if len(parts) > 2 and parts[0] == arena_lib.AGENTS_DIR:
            agent_id = existing.get(f"{parts[0]}/{parts[1]}")
            if agent_id:
                ids.add(agent_id)
    return sorted(ids)


def _diff(base: str, root: Path) -> list[str] | None:
    if not base or set(base) == {"0"}:
        return None
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"], cwd=root, capture_output=True, text=True
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.split()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List agents changed since a git ref.")
    parser.add_argument("--base", default="", help="git ref (empty or all zeros: every agent)")
    parser.add_argument("--root", type=Path, default=arena_lib.REPO_ROOT)
    args = parser.parse_args(argv)
    paths = _diff(args.base, args.root)
    if paths is None:
        ids = [a.id for a in arena_lib.discover(args.root)]
    else:
        ids = changed_ids(paths, args.root)
    print(json.dumps(ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
