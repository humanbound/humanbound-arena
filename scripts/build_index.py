# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
"""Generate index.json (the catalog hb reads) and the README catalog table from agents/.

    python scripts/build_index.py                                # rewrite index.json and README.md
    python scripts/build_index.py --check                        # exit 1 if either is out of date
    python scripts/build_index.py --health-results results.json  # also record nightly smoke results

CI runs this on main; never edit index.json by hand. Rebuilding with nothing changed leaves both
files byte-for-byte identical (generated_at only moves when the content does). The CLI ignores the
extra fields used here: categories, references, developer, health and deprecated.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import UTC, date, datetime
from pathlib import Path

import standards

import arena_lib
from arena_lib import Agent

SCHEMA_VERSION = 1
# The first hb release with `hb arena`; the CLI refuses a catalog that needs a newer hb than the
# one installed. Bump it when the catalog needs newer CLI features, and keep it in step with
# HB_PIP_SPEC in .github/workflows/.
MIN_HB_VERSION = "2.12.0"
DEPRECATE_AFTER_DAYS = 30
CATALOG_START, CATALOG_END = "<!-- catalog:start -->", "<!-- catalog:end -->"
CATEGORY_NAMES = {
    **{f"llm{int(k[3:]):03d}": f"{k} {v}" for k, v in standards.OWASP_LLM.items()},
    **{k.lower(): f"{k} {v}" for k, v in standards.OWASP_AGENTIC.items()},
}


def _timestamp(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _failing_days(health: dict | None, today: date) -> int:
    if not health or health.get("status") != "failing" or not health.get("failing_since"):
        return 0
    return (today - date.fromisoformat(health["failing_since"])).days


def _images_for(agent: Agent, previous: dict | None, digests: dict[str, str]) -> list[dict]:
    """The agent's images, in build order, with whatever digests we know for them.

    CI builds only the agents that changed, so a run produces metadata for those alone. Digests
    already in the catalog are carried forward for every other ref; that is safe because image
    tags are immutable, so a given ref's digest never changes. An image we have never published
    is simply omitted, and hb falls back to the tag.
    """
    known = {i["ref"]: i["digest"] for i in (previous or {}).get("images", []) if "digest" in i}
    # `digests` is the whole run's metadata and may hold refs for other agents; the loop
    # below narrows it to this agent's build tags.
    known.update(digests)
    images = []
    for build in arena_lib.image_builds(agent):
        if build.tag in known:
            images.append({"ref": build.tag, "digest": known[build.tag]})
    return images


def entry_for(
    agent: Agent, previous: dict | None, today: date, digests: dict[str, str] | None = None
) -> dict:
    m = agent.manifest
    if not agent.developer.get("name"):
        raise SystemExit(
            f"{agent.id}: arena.yaml needs a developer block with a name, or the catalog would "
            f"publish the agent unattributed; scripts/policy_check.py checks the rest of the block"
        )
    ground_truth = m.get("ground_truth") or {}
    entry = {
        "id": agent.id,
        "version": agent.version,
        "name": m.get("name", agent.id),
        "description": m.get("description", ""),
        "tags": list(m.get("tags") or []),
        "difficulty": m.get("difficulty", ""),
        "developer": dict(agent.developer),
        "manifest_url": f"{agent.rel_path}/arena.yaml",
        "categories": sorted(
            {
                gt["category"]
                for gt in ground_truth.values()
                if isinstance(gt, dict) and "category" in gt
            }
        ),
        "references": sorted(
            {
                ref
                for gt in ground_truth.values()
                if isinstance(gt, dict)
                for ref in gt.get("references") or []
            }
        ),
    }
    images = _images_for(agent, previous, digests or {})
    if images:
        entry["images"] = images
    health = copy.deepcopy((previous or {}).get("health"))
    if health:
        entry["health"] = health
    if (agent.path / "DEPRECATED").exists() or _failing_days(health, today) >= DEPRECATE_AFTER_DAYS:
        entry["deprecated"] = True
    return entry


def build_index(
    agents: list[Agent],
    previous: dict | None,
    now: datetime,
    digests: dict[str, str] | None = None,
) -> dict:
    today = now.astimezone(UTC).date()
    before = {e["id"]: e for e in (previous or {}).get("agents", [])}
    entries = [
        entry_for(a, before.get(a.id), today, digests) for a in sorted(agents, key=lambda a: a.id)
    ]
    index = {
        "schema_version": SCHEMA_VERSION,
        "min_hb_version": MIN_HB_VERSION,
        "generated_at": _timestamp(now),
        "agents": entries,
    }
    if previous and {**previous, "generated_at": None} == {**index, "generated_at": None}:
        index["generated_at"] = previous.get("generated_at", index["generated_at"])
    return index


def apply_health(index: dict, results: dict[str, bool], today: date) -> dict:
    """Record nightly smoke results (id -> passed) in each entry's `health`.

    Health changes only when an agent starts or stops failing. Every change to the catalog is a
    pull request, so a night in which nothing changed must leave the catalog as it was.
    """
    for entry in index.get("agents", []):
        if entry["id"] not in results:
            continue
        if results[entry["id"]]:
            entry["health"] = {"status": "passing"}
        else:
            old = entry.get("health") or {}
            since = old.get("failing_since") if old.get("status") == "failing" else None
            entry["health"] = {"status": "failing", "failing_since": since or today.isoformat()}
    return index


def _health_cell(entry: dict) -> str:
    health = entry.get("health") or {}
    if health.get("status") == "passing":
        return "✅ passing"
    if health.get("status") == "failing":
        return f"❌ failing since {health.get('failing_since', '?')}"
    return "—"


def render_catalog(index: dict) -> str:
    agents = index.get("agents") or []
    if not agents:
        return "_No agents yet._"
    rows = [
        "| Agent | What it teaches | Category | Difficulty | Health |",
        "|---|---|---|---|---|",
    ]
    for e in agents:
        path = e["manifest_url"].rsplit("/", 1)[0]
        name = f"[{e['name']}]({path})" + (" (deprecated)" if e.get("deprecated") else "")
        categories = "<br>".join(CATEGORY_NAMES.get(c, c) for c in e.get("categories", []))
        description = e.get("description", "").replace("|", "\\|").replace("\n", " ")
        rows.append(
            f"| {name} | {description} | {categories} | {e.get('difficulty', '')} | {_health_cell(e)} |"
        )
    return "\n".join(rows)


def update_readme(text: str, table: str) -> str:
    start, end = text.find(CATALOG_START), text.find(CATALOG_END)
    if start < 0 or end < start:
        raise ValueError(f"README.md needs {CATALOG_START} and {CATALOG_END} markers")
    return text[: start + len(CATALOG_START)] + "\n" + table + "\n" + text[end:]


def _dump(index: dict) -> str:
    return json.dumps(index, indent=2, ensure_ascii=False) + "\n"


def _load_results(path: Path) -> dict[str, bool]:
    return {r["id"]: bool(r["ok"]) for r in json.loads(path.read_text(encoding="utf-8"))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate index.json and the README catalog.")
    parser.add_argument("--root", type=Path, default=arena_lib.REPO_ROOT)
    parser.add_argument("--health-results", type=Path, help="smoke.py --json-out results")
    parser.add_argument(
        "--digests",
        help="JSON file of {image ref: sha256 digest} from build_images.py --digests-out",
    )
    parser.add_argument("--check", action="store_true", help="exit 1 if anything would change")
    args = parser.parse_args(argv)

    index_path, readme_path = args.root / "index.json", args.root / "README.md"
    previous = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else None
    now = datetime.now(UTC)
    digests = json.loads(Path(args.digests).read_text()) if args.digests else None
    index = build_index(arena_lib.discover(args.root), previous, now, digests=digests)
    if args.health_results:
        apply_health(index, _load_results(args.health_results), now.date())
        index = build_index(arena_lib.discover(args.root), index, now)  # re-derive deprecation
        if previous and {**previous, "generated_at": None} != {**index, "generated_at": None}:
            index["generated_at"] = _timestamp(now)
    readme = readme_path.read_text(encoding="utf-8")
    new_readme = update_readme(readme, render_catalog(index))
    new_index = _dump(index)

    old_index = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    changed = [
        p.name
        for p, old, new in (
            (index_path, old_index, new_index),
            (readme_path, readme, new_readme),
        )
        if old != new
    ]
    if args.check:
        if changed:
            print(f"out of date: {', '.join(changed)} → run python scripts/build_index.py")
            return 1
        print("index.json and README.md are up to date")
        return 0
    index_path.write_text(new_index, encoding="utf-8")
    readme_path.write_text(new_readme, encoding="utf-8")
    print(
        f"wrote {', '.join(changed) or 'nothing (already up to date)'}; {len(index['agents'])} agent(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
