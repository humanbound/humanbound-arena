# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
import json
import subprocess
from pathlib import Path

import pytest

import arena_lib
import build_images
from conftest import make_agent


class FakeRun:
    def __init__(self, existing=()):
        self.calls = []
        self.existing = set(existing)

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        rc = 0
        if argv[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            rc = 0 if argv[4] in self.existing else 1
        return subprocess.CompletedProcess(argv, rc, "", "")


def test_local_build_argv(repo):
    path = make_agent(repo)
    (build,) = arena_lib.image_builds(arena_lib.find_agent("demo", repo))
    assert build_images.build_argv(build, push=False, platforms=None) == [
        "docker", "build", "-t", "ghcr.io/humanbound/arena-demo:1.0.0",
        "-f", str(path / "services" / "agent" / "Dockerfile"), str(path),
    ]  # fmt: skip


def test_push_build_argv(repo):
    path = make_agent(repo)
    (build,) = arena_lib.image_builds(arena_lib.find_agent("demo", repo))
    argv = build_images.build_argv(build, push=True, platforms="linux/amd64,linux/arm64")
    assert argv == [
        "docker", "buildx", "build", "--platform", "linux/amd64,linux/arm64", "--push",
        "-t", "ghcr.io/humanbound/arena-demo:1.0.0",
        "-f", str(path / "services" / "agent" / "Dockerfile"), str(path),
    ]  # fmt: skip


def test_push_refuses_an_existing_tag(repo):
    make_agent(repo)
    run = FakeRun(existing={"ghcr.io/humanbound/arena-demo:1.0.0"})
    with pytest.raises(SystemExit, match="already exists"):
        build_images.build_agent(arena_lib.find_agent("demo", repo), push=True, run=run)
    assert not any(c[:3] == ["docker", "buildx", "build"] for c in run.calls)


def test_push_builds_a_new_tag(repo):
    make_agent(repo)
    run = FakeRun()
    build_images.build_agent(arena_lib.find_agent("demo", repo), push=True, run=run)
    assert run.calls[-1][:3] == ["docker", "buildx", "build"]


def test_a_failed_build_raises(repo):
    make_agent(repo)

    def failing(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "")

    with pytest.raises(SystemExit, match="failed"):
        build_images.build_agent(arena_lib.find_agent("demo", repo), run=failing)


def test_skip_existing_publishes_nothing_for_a_tag_already_pushed(repo, capsys):
    make_agent(repo)
    run = FakeRun(existing={"ghcr.io/humanbound/arena-demo:1.0.0"})
    agent = arena_lib.find_agent("demo", repo)
    build_images.build_agent(agent, push=True, skip_existing=True, run=run)
    assert not any(c[:3] == ["docker", "buildx", "build"] for c in run.calls)
    assert "already published" in capsys.readouterr().out


def test_build_argv_asks_buildx_for_metadata_when_pushing(tmp_path):
    build = arena_lib.ImageBuild(
        tag="ghcr.io/humanbound/arena-demo:1.0.0",
        context=tmp_path,
        dockerfile=tmp_path / "services/agent/Dockerfile",
    )
    meta = tmp_path / "meta.json"
    argv = build_images.build_argv(build, push=True, platforms=None, metadata_file=meta)
    assert "--metadata-file" in argv
    assert argv[argv.index("--metadata-file") + 1] == str(meta)
    # A local build has no registry to report a digest from.
    assert "--metadata-file" not in build_images.build_argv(
        build, push=False, platforms=None, metadata_file=meta
    )


def test_digests_out_merges_across_invocations(repo, tmp_path):
    make_agent(repo, "demo")
    agent = arena_lib.find_agent("demo", repo)
    out = tmp_path / "digests.json"
    out.write_text(json.dumps({"ghcr.io/humanbound/arena-other:1.0.0": "sha256:" + "b" * 64}))

    def run(argv, **kwargs):
        if "--metadata-file" in argv:
            path = Path(argv[argv.index("--metadata-file") + 1])
            path.write_text(json.dumps({"containerimage.digest": "sha256:" + "a" * 64}))
            return subprocess.CompletedProcess(argv, 0)
        return subprocess.CompletedProcess(argv, 1)  # tag_exists -> not published

    build_images.build_agent(agent, push=True, digests_out=out, run=run)
    assert json.loads(out.read_text()) == {
        "ghcr.io/humanbound/arena-other:1.0.0": "sha256:" + "b" * 64,
        "ghcr.io/humanbound/arena-demo:1.0.0": "sha256:" + "a" * 64,
    }


def test_a_push_with_no_digest_in_the_metadata_warns_and_records_nothing(repo, tmp_path, capsys):
    make_agent(repo, "demo")
    agent = arena_lib.find_agent("demo", repo)
    out = tmp_path / "digests.json"

    def run(argv, **kwargs):
        if "--metadata-file" in argv:
            path = Path(argv[argv.index("--metadata-file") + 1])
            path.write_text(json.dumps({"unrelated": "field"}))  # no containerimage.digest
            return subprocess.CompletedProcess(argv, 0)
        return subprocess.CompletedProcess(argv, 1)  # tag_exists -> not published

    build_images.build_agent(agent, push=True, digests_out=out, run=run)
    assert "warning: pushed ghcr.io/humanbound/arena-demo:1.0.0 but found no digest" in (
        capsys.readouterr().out
    )
    assert not out.exists()
