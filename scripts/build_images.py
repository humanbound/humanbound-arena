# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
"""Build an agent's images (see arena_lib.image_builds for which ones).

    python scripts/build_images.py hello-world                     # local, single arch
    python scripts/build_images.py hello-world --push \\
        --platforms linux/amd64,linux/arm64                        # publish (CI)

Publishing refuses a tag that already exists: image tags are immutable, so every change to an
agent needs a version bump.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import arena_lib
from arena_lib import Agent, ImageBuild

Runner = Callable[..., subprocess.CompletedProcess]


def build_argv(
    build: ImageBuild,
    *,
    push: bool,
    platforms: str | None,
    metadata_file: Path | None = None,
) -> list[str]:
    target = ["-t", build.tag, "-f", str(build.dockerfile), str(build.context)]
    if push:
        meta = ["--metadata-file", str(metadata_file)] if metadata_file else []
        return [
            "docker", "buildx", "build", "--platform", platforms or "linux/amd64", "--push",
            *meta, *target,
        ]  # fmt: skip
    return ["docker", "build", *target]


def tag_exists(tag: str, run: Runner = subprocess.run) -> bool:
    proc = run(["docker", "buildx", "imagetools", "inspect", tag], capture_output=True, text=True)
    return proc.returncode == 0


def _digest_from(metadata_file: Path) -> str | None:
    """The pushed image's content digest, as buildx reports it."""
    try:
        meta = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    digest = meta.get("containerimage.digest")
    return str(digest) if digest else None


def _merge_digests(path: Path, found: dict[str, str]) -> None:
    """Add `found` to the digest file, keeping what earlier invocations wrote.

    publish.yml calls this script once per changed agent, so each run must extend the file
    rather than replace it. Unreadable JSON is discarded and rebuilt, but an OSError is
    deliberately left to propagate: the file holds every earlier agent's digests, and
    silently starting from scratch would publish a catalog missing pins it should have.
    """
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            existing = {}
    path.write_text(json.dumps({**existing, **found}, indent=2, sort_keys=True) + "\n")


def build_agent(
    agent: Agent,
    *,
    push: bool = False,
    platforms: str | None = None,
    skip_existing: bool = False,
    digests_out: Path | None = None,
    run: Runner = subprocess.run,
) -> None:
    """Build (and with push=True, publish) the agent's images.

    Publishing an existing tag fails, unless skip_existing: then an agent whose tags are all
    published already is skipped (CI rebuilds nothing when only the tooling changed; PRs are held
    to a version bump by policy_check.py). With digests_out, each pushed image's content digest
    is recorded there for build_index.py to put in the catalog.
    """
    builds = arena_lib.image_builds(agent)
    if not builds:
        raise SystemExit(f"{agent.id}: no images to build")
    if push:
        existing = [b.tag for b in builds if tag_exists(b.tag, run)]
        if existing and skip_existing and len(existing) == len(builds):
            print(f"{agent.id} {agent.version}: already published, skipping", flush=True)
            return
        if existing:
            raise SystemExit(f"{', '.join(existing)} already exists; bump {agent.id}'s version")
    found: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix=f"arena-build-{agent.id}-") as tmp:
        for i, build in enumerate(builds):
            print(f"building {build.tag}", flush=True)
            meta = Path(tmp) / f"{i}.json" if push and digests_out else None
            if run(
                build_argv(build, push=push, platforms=platforms, metadata_file=meta)
            ).returncode:
                raise SystemExit(f"building {build.tag} failed")
            if meta is not None:
                if digest := _digest_from(meta):
                    found[build.tag] = digest
                else:
                    print(
                        f"warning: pushed {build.tag} but found no digest in buildx metadata; "
                        f"the catalog will fall back to the tag for it",
                        flush=True,
                    )
    if digests_out is not None and found:
        _merge_digests(Path(digests_out), found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an agent's images.")
    parser.add_argument("ids", nargs="+")
    parser.add_argument("--push", action="store_true", help="build with buildx and push")
    parser.add_argument("--platforms", default="linux/amd64,linux/arm64")
    parser.add_argument(
        "--skip-existing", action="store_true", help="with --push: skip agents already published"
    )
    parser.add_argument(
        "--digests-out", help="with --push: append {image ref: digest} to this JSON file"
    )
    args = parser.parse_args(argv)
    for agent_id in args.ids:
        build_agent(
            arena_lib.find_agent(agent_id),
            push=args.push,
            platforms=args.platforms,
            skip_existing=args.skip_existing,
            digests_out=Path(args.digests_out) if args.digests_out else None,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
