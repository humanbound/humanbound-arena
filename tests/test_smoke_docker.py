# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
"""End-to-end smoke of real agents: needs Docker and hb. Run with `pytest -m docker`."""

import shutil
import subprocess

import pytest

import arena_lib
import smoke

pytestmark = pytest.mark.docker


def _docker_up() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _docker_up() or shutil.which("hb") is None, reason="needs Docker and hb")
@pytest.mark.parametrize("agent_id", [a.id for a in arena_lib.discover()])
def test_agent_smoke(agent_id):
    result = smoke.run_smoke(arena_lib.find_agent(agent_id))
    assert result.ok, result.detail
