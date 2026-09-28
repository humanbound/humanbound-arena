# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
"""scripts/open_catalog_pr.sh, against a real git remote and a stand-in for the gh command."""

import json
import os
import subprocess

import pytest

import arena_lib

SCRIPT = arena_lib.REPO_ROOT / "scripts" / "open_catalog_pr.sh"

# Records every call, and answers `gh pr list` from a file the test writes.
FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$GH_LOG"
if [ "$1 $2" = "pr create" ] && [ -n "${GH_REFUSES_TO_CREATE:-}" ]; then
  echo "GitHub Actions is not permitted to create or approve pull requests" >&2
  exit 1
fi
if [ "$1 $2" = "pr list" ]; then
  python3 - "$GH_OPEN" <<'EOF'
import json, sys
for pr in json.load(open(sys.argv[1])):
    print(pr["number"], pr["headRefName"])
EOF
fi
"""


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip


@pytest.fixture
def checkout(tmp_path):
    """A clone of a remote whose main holds a catalog, with a stand-in gh on the PATH."""
    remote, work, tools = tmp_path / "remote.git", tmp_path / "work", tmp_path / "bin"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    _git(tmp_path, "clone", "-q", str(remote), str(work))
    (work / "index.json").write_text('{"agents": []}\n')
    (work / "README.md").write_text("catalog\n")
    (work / "other.txt").write_text("not the catalog\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "base")
    _git(work, "push", "-q", "origin", "HEAD:main")
    tools.mkdir()
    (tools / "gh").write_text(FAKE_GH)
    (tools / "gh").chmod(0o755)
    (tmp_path / "open.json").write_text("[]")
    env = dict(
        os.environ,
        PATH=f"{tools}{os.pathsep}{os.environ['PATH']}",
        GH_LOG=str(tmp_path / "gh.log"),
        GH_OPEN=str(tmp_path / "open.json"),
        GITHUB_RUN_ID="42",
        GITHUB_RUN_ATTEMPT="1",
    )

    def run():
        done = subprocess.run(
            ["bash", str(SCRIPT), "chore: regenerate index.json"],
            cwd=work, env=env, capture_output=True, text=True,
        )  # fmt: skip
        log = tmp_path / "gh.log"
        calls = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return done, calls

    return work, remote, tmp_path, run


def _branches(remote):
    return sorted(_git(remote, "for-each-ref", "--format=%(refname:short)", "refs/heads").split())


def test_an_unchanged_catalog_proposes_nothing(checkout):
    work, remote, _, run = checkout
    done, calls = run()
    assert done.returncode == 0, done.stderr
    assert "has not changed" in done.stdout
    assert calls == [] and _branches(remote) == ["main"]


def test_a_changed_catalog_becomes_a_pull_request_and_main_is_not_touched(checkout):
    work, remote, _, run = checkout
    main_before = _git(remote, "rev-parse", "main")
    (work / "index.json").write_text('{"agents": [1]}\n')
    (work / "other.txt").write_text("changed too, but it is not the catalog\n")
    done, calls = run()
    assert done.returncode == 0, done.stderr
    assert _branches(remote) == ["catalog/42-1", "main"]
    assert _git(remote, "rev-parse", "main") == main_before
    assert _git(remote, "show", "--name-only", "--format=", "catalog/42-1").split() == [
        "index.json"
    ]
    message = _git(remote, "log", "-1", "--format=%B", "catalog/42-1")
    assert message.startswith("chore: regenerate index.json")
    assert "Signed-off-by: github-actions[bot]" in message
    assert any(c.startswith("pr create --base main --head catalog/42-1") for c in calls)
    # the checks main requires are started on the new branch
    assert "workflow run validate.yml --ref catalog/42-1" in calls
    assert "workflow run dco.yml --ref catalog/42-1" in calls


def test_the_same_change_is_not_proposed_twice(checkout):
    work, remote, tmp, run = checkout
    (work / "index.json").write_text('{"agents": [1]}\n')
    run()
    (tmp / "open.json").write_text(json.dumps([{"number": 7, "headRefName": "catalog/42-1"}]))
    _git(work, "switch", "-q", "--detach", "origin/main")  # the next run starts from main again
    (work / "index.json").write_text('{"agents": [1]}\n')
    done, calls = run()
    assert done.returncode == 0, done.stderr
    assert "already proposed in #7" in done.stdout
    assert not any(c.startswith(("pr create", "pr close", "workflow run")) for c in calls)
    assert _branches(remote) == ["catalog/42-1", "main"]


def test_a_later_run_replaces_the_open_pull_request(checkout, monkeypatch):
    work, remote, tmp, run = checkout
    (work / "index.json").write_text('{"agents": [1]}\n')
    run()
    (tmp / "open.json").write_text(json.dumps([{"number": 7, "headRefName": "catalog/42-1"}]))
    _git(work, "switch", "-q", "--detach", "origin/main")
    (work / "index.json").write_text('{"agents": [1, 2]}\n')
    done = subprocess.run(
        ["bash", str(SCRIPT), "chore: regenerate index.json"],
        cwd=work, capture_output=True, text=True,
        env=dict(
            os.environ,
            PATH=f"{tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
            GH_LOG=str(tmp / "gh.log"), GH_OPEN=str(tmp / "open.json"),
            GITHUB_RUN_ID="43", GITHUB_RUN_ATTEMPT="1",
        ),
    )  # fmt: skip
    calls = (tmp / "gh.log").read_text().splitlines()
    assert done.returncode == 0, done.stderr
    assert any(c.startswith("pr create --base main --head catalog/43-1") for c in calls)
    assert any(c.startswith("pr close 7 --delete-branch") for c in calls)
    assert calls.index(next(c for c in calls if c.startswith("pr create"))) < calls.index(
        next(c for c in calls if c.startswith("pr close"))
    ), "open the new one before closing the old, so a failure never leaves none"


def test_when_ci_may_not_open_pull_requests_it_says_what_to_do(checkout, monkeypatch):
    work, remote, _, run = checkout
    monkeypatch.setenv("GH_REFUSES_TO_CREATE", "1")
    main_before = _git(remote, "rev-parse", "main")
    (work / "index.json").write_text('{"agents": [1]}\n')
    done = subprocess.run(
        ["bash", str(SCRIPT), "chore: regenerate index.json"],
        cwd=work, capture_output=True, text=True,
        env=dict(
            os.environ,
            PATH=f"{work.parent / 'bin'}{os.pathsep}{os.environ['PATH']}",
            GH_LOG=str(work.parent / "gh.log"), GH_OPEN=str(work.parent / "open.json"),
            GITHUB_RUN_ID="42", GITHUB_RUN_ATTEMPT="1",
        ),
    )  # fmt: skip
    calls = (work.parent / "gh.log").read_text().splitlines()
    assert done.returncode == 1, "a catalog update that nobody will see must not look like success"
    assert "catalog/42-1" in done.stdout and "by hand" in done.stdout
    assert "Allow GitHub Actions to create and approve pull requests" in done.stdout
    # the work is not lost, and nothing was done behind a pull request that does not exist
    assert _branches(remote) == ["catalog/42-1", "main"]
    assert _git(remote, "rev-parse", "main") == main_before
    assert not any(c.startswith(("workflow run", "pr close")) for c in calls)
