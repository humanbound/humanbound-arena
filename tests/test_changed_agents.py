# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
import json
import subprocess

import changed_agents
from conftest import make_agent


def test_changed_ids_maps_paths_to_existing_agents(repo):
    make_agent(repo, "shop")
    make_agent(repo, "other")
    paths = [
        "agents/shop/services/agent/app.py",
        "agents/other/README.md",
        "agents/deleted/arena.yaml",
        "docs/agent-anatomy.md",
        "README.md",
    ]
    assert changed_agents.changed_ids(paths, repo) == ["other", "shop"]


def test_tooling_changes_count_as_every_agent_changing(repo):
    make_agent(repo, "shop")
    make_agent(repo, "other")
    assert changed_agents.changed_ids(["scripts/smoke.py"], repo) == ["other", "shop"]
    assert changed_agents.changed_ids(["tools/mock-llm/mock_llm.py"], repo) == ["other", "shop"]


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True,
    )  # fmt: skip


def test_main_diffs_against_a_base_and_falls_back_to_all(repo, capsys):
    make_agent(repo, "shop")
    make_agent(repo, "other")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    (repo / "agents" / "shop" / "README.md").write_text("changed\n")
    _git(repo, "commit", "-qam", "change shop")
    assert changed_agents.main(["--root", str(repo), "--base", "HEAD~1"]) == 0
    assert json.loads(capsys.readouterr().out) == ["shop"]
    for base in ("", "0" * 40, "no-such-ref"):
        assert changed_agents.main(["--root", str(repo), "--base", base]) == 0
        assert json.loads(capsys.readouterr().out) == ["other", "shop"]
