# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import arena_lib
import smoke
from conftest import make_agent


@pytest.mark.parametrize("system", ["Darwin", "Windows"])
def test_llm_host_on_docker_desktop(system):
    assert smoke.llm_host(system, {}) == "host.docker.internal"


def test_llm_host_on_linux_uses_the_default_route_ip(monkeypatch):
    monkeypatch.setattr(smoke, "default_route_ip", lambda: "10.1.2.3")
    assert smoke.llm_host("Linux", {}) == "10.1.2.3"


def test_llm_host_override():
    assert smoke.llm_host("Linux", {"SMOKE_LLM_HOST": "172.17.0.1"}) == "172.17.0.1"


def test_write_index_lists_one_agent_with_an_absolute_manifest(repo, tmp_path):
    path = make_agent(repo)
    index_path = smoke.write_index(arena_lib.find_agent("demo", repo), tmp_path)
    index = json.loads(index_path.read_text())
    (entry,) = index["agents"]
    assert entry["id"] == "demo" and entry["version"] == "1.0.0"
    assert Path(entry["manifest_url"]) == path / "arena.yaml"
    catalog = pytest.importorskip("humanbound_cli.arena.catalog")
    loaded = catalog.load_index(str(index_path))
    manifest = catalog.read_manifest(catalog.resolve(loaded.index, "demo"), loaded.location)
    assert manifest.id == "demo"


def test_write_env_file_is_private(tmp_path):
    path = smoke.write_env_file(tmp_path / "agent.env", {"A": "1", "B": "two"})
    assert path.read_text() == "A=1\nB=two\n"
    assert path.stat().st_mode & 0o777 == 0o600


class _Gateway:
    """A stand-in gateway that answers SendMessage with a canned JSON-RPC body."""

    def __init__(self, body):
        outer = self
        self.requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                outer.requests.append(
                    (self.path, dict(self.headers), json.loads(self.rfile.read(length)))
                )
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def test_send_posts_a2a_send_message_and_returns_the_text():
    body = {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {"message": {"role": "ROLE_AGENT", "parts": [{"text": "hel"}, {"text": "lo"}]}},
    }
    gw = _Gateway(body)
    try:
        assert smoke.send(gw.url, "demo", "hi", "ctx-1", "tok-123") == "hello"
    finally:
        gw.close()
    path, headers, request = gw.requests[0]
    assert path == "/a2a/demo"
    assert headers["A2A-Version"] == "1.0"
    assert headers["Authorization"] == "Bearer tok-123"
    assert request["method"] == "SendMessage"
    message = request["params"]["message"]
    assert message["parts"] == [{"text": "hi"}] and message["contextId"] == "ctx-1"


def test_send_raises_on_a_json_rpc_error():
    gw = _Gateway({"jsonrpc": "2.0", "id": "1", "error": {"code": -32603, "message": "agent down"}})
    try:
        with pytest.raises(smoke.SmokeError, match="agent down"):
            smoke.send(gw.url, "demo", "hi", "ctx-1", "tok-123")
    finally:
        gw.close()


def test_gateway_token_is_read_from_the_arena_dir(tmp_path):
    arena = tmp_path / ".humanbound" / "arena"
    arena.mkdir(parents=True)
    (arena / "gateway.token").write_text("a" * 43 + "\n")
    assert smoke.gateway_token({"HOME": str(tmp_path)}) == "a" * 43


def test_gateway_token_missing_is_a_smoke_error(tmp_path):
    with pytest.raises(smoke.SmokeError, match="cannot read the gateway token"):
        smoke.gateway_token({"HOME": str(tmp_path)})


def test_hb_env_isolates_home_and_keeps_docker_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/Users/someone")
    monkeypatch.delenv("DOCKER_CONFIG", raising=False)
    env = smoke.hb_env(tmp_path, tmp_path / "index.json", 12345)
    assert env["HOME"] == str(tmp_path / "home")
    assert env["DOCKER_CONFIG"] == "/Users/someone/.docker"
    assert env["HB_ARENA_INDEX"] == str(tmp_path / "index.json")
    assert env["HB_ARENA_PORT"] == "12345"


def test_main_rejects_unknown_ids(capsys):
    assert smoke.main(["no-such-agent"]) == 2
    assert "no-such-agent" in capsys.readouterr().err


def _checked(returncode, stdout="", stderr=""):
    return subprocess.CompletedProcess(["hb", "arena", "check"], returncode, stdout, stderr)


def _report(containers, violations=()):
    return json.dumps(
        {"agent": "demo", "containers": list(containers), "violations": list(violations)}
    )


def test_a_passing_baseline_reports_how_many_containers_were_checked():
    assert smoke.baseline(_checked(0, _report(["arena-abc-demo", "arena-abc-shop"]))) == (2, [])


def test_violations_are_reported_by_container_and_rule():
    broken = [
        {"container": "arena-abc-demo", "rule": "non-root", "message": "runs as root"},
        {"container": "arena-abc-demo", "rule": "host-mounts", "message": "mounts /tmp"},
    ]
    count, problems = smoke.baseline(_checked(1, _report(["arena-abc-demo"], broken)))
    assert count == 1
    assert problems == [
        "arena-abc-demo: non-root: runs as root",
        "arena-abc-demo: host-mounts: mounts /tmp",
    ]


def test_a_check_that_could_not_run_is_a_failure_not_a_pass():
    count, problems = smoke.baseline(_checked(2, "", "demo is not running → hb arena run demo\n"))
    assert count == 0
    assert problems == ["hb arena check could not run: demo is not running → hb arena run demo"]


@pytest.mark.parametrize(
    "result",
    [
        _checked(0, ""),  # says it passed, reports nothing
        _checked(0, "not json"),
        _checked(0, _report([])),  # nothing was looked at
        _checked(0, json.dumps({"agent": "demo"})),
        _checked(1, _report(["arena-abc-demo"])),  # says it failed, names no problem
        _checked(0, _report(["arena-abc-demo"], [{"container": "x", "rule": "r", "message": "m"}])),
    ],
)
def test_an_answer_that_does_not_add_up_is_a_failure(result):
    _, problems = smoke.baseline(result)
    assert problems, "an unreadable or contradictory answer must never count as a pass"
