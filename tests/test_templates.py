# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
"""New agents start from an existing one (docs/adding-an-agent.md), so a renamed copy of the
reference agent, hello-world, must stay valid."""

import shutil

import pytest

import arena_lib
import policy_check

JUNK = shutil.ignore_patterns(".DS_Store", "__pycache__", "*.pyc", ".pytest_cache")


def _copy(src_rel, dest, old_id, new_id):
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(arena_lib.REPO_ROOT / src_rel, dest, ignore=JUNK)
    manifest = dest / "arena.yaml"
    text = manifest.read_text().replace(f"id: {old_id}", f"id: {new_id}")
    manifest.write_text(text.replace(f"arena-{old_id}:", f"arena-{new_id}:"))
    return arena_lib.load_agent(dest)


def test_a_renamed_copy_of_hello_world_is_a_valid_agent(tmp_path):
    agent = _copy("agents/hello-world", tmp_path / "agents" / "my-agent", "hello-world", "my-agent")
    assert policy_check.check_agent(agent) == []
    hb_manifest = pytest.importorskip("humanbound_cli.arena.manifest")
    assert hb_manifest.parse_manifest(agent.manifest).id == "my-agent"
