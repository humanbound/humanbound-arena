# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 AI and Me Single-Member Private Company (Humanbound)
"""The firewall's policy file and the manifest's agent: block (hb test's scope) are the same policy."""
from pathlib import Path

import agent_core
import yaml

MANIFEST = Path(__file__).resolve().parents[1] / "arena.yaml"


def test_the_policy_file_is_the_manifest_agent_block():
    policy = yaml.safe_load(agent_core.POLICY.read_text())
    assert policy == yaml.safe_load(MANIFEST.read_text())["agent"]
    for key in ("name", "scope", "intents", "tools", "settings"):
        assert key in policy
