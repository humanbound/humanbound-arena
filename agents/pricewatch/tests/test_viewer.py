# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 AI and Me Single-Member Private Company (Humanbound)
"""The walkthrough page's server: the page, and read-only the two files its code drawer shows."""
import importlib.util
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "lab" / "ui"
AGENT = Path(__file__).resolve().parents[1] / "services" / "agent"


@pytest.fixture(scope="module")
def viewer():
    spec = importlib.util.spec_from_file_location("viewer", UI / "viewer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.handler("http://127.0.0.1:9"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as reply:
        return reply.status, reply.read().decode()


def test_the_drawer_reads_the_agents_real_files(viewer):
    assert _get(f"{viewer}/code/agent_core.py") == (200, (AGENT / "agent_core.py").read_text())
    assert _get(f"{viewer}/code/agent.yaml") == (200, (AGENT / "agent.yaml").read_text())


@pytest.mark.parametrize("path", ["/code/db.py", "/code/../agent.yaml", "/code/../../arena.yaml",
                                  "/code/", "/viewer.py"])
def test_nothing_else_is_served(viewer, path):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(viewer + path)
    assert e.value.code == 404


def test_the_page_points_at_the_code_the_drawer_cuts_from_it():
    """The drawer cuts agent_core.py at these lines: they must still be there."""
    page = (UI / "index.html").read_text()
    code = (AGENT / "agent_core.py").read_text()
    for marker in ("# The firewall's policy", "# ---- the firewall's log", "def build_agent(", "def build_task("):
        assert marker in page and marker in code
