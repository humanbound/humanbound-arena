# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
"""Smoke-test agents end to end: build, `hb arena run`, talk to them through the gateway.

    python scripts/smoke.py hello-world                # one agent (Docker + hb needed)
    python scripts/smoke.py --all --json-out r.json    # every agent, results for build_index
    python scripts/smoke.py hello-world --real-llm     # a real model instead of the mock

Each run builds the agent's images locally (with the exact tags its manifest names, so hb finds
them and never pulls), starts the scripted mock LLM from tests/smoke.yaml, and runs hb against a
throwaway HOME and a one-agent index.json. Keys reach the agent through --env-file, never the
shell. Smoke proves the plumbing works, not the vulnerability.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import arena_lib
import build_images
from arena_lib import Agent

sys.path.insert(0, str(arena_lib.REPO_ROOT / "tools" / "mock-llm"))
from mock_llm import MockLLM  # noqa: E402

FAKE_KEY = "sk-arena-FAKE-mock"
A2A_VERSION = "1.0"


class SmokeError(RuntimeError):
    pass


@dataclass
class SmokeResult:
    id: str
    ok: bool
    detail: str
    seconds: float = 0.0


def default_route_ip() -> str:
    """This host's address on its default route (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed anywhere real
        return s.getsockname()[0]


def llm_host(system: str | None = None, env: dict | None = None) -> str:
    """How a container reaches the mock LLM running on this host."""
    env = os.environ if env is None else env
    if env.get("SMOKE_LLM_HOST"):
        return env["SMOKE_LLM_HOST"]
    if (system or platform.system()) in ("Darwin", "Windows"):
        return "host.docker.internal"
    return default_route_ip()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_index(agent: Agent, dest: Path) -> Path:
    """A one-agent index.json in `dest`, pointing at the checkout's arena.yaml."""
    m = agent.manifest
    index = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "agents": [
            {
                "id": agent.id,
                "version": agent.version,
                "name": m.get("name", agent.id),
                "description": m.get("description", ""),
                "tags": m.get("tags") or [],
                "difficulty": m.get("difficulty", ""),
                "manifest_url": str((agent.path / "arena.yaml").resolve()),
            }
        ],
    }
    path = Path(dest) / "index.json"
    path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return path


def write_env_file(path: Path, values: dict[str, str]) -> Path:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.writelines(f"{k}={v}\n" for k, v in values.items())
    return path


def hb_env(workdir: Path, index_path: Path, port: int) -> dict[str, str]:
    """The environment hb runs in: a fresh HOME (so a fresh arena cache) and our own gateway port."""
    env = dict(os.environ)
    real_home = env.get("HOME", str(Path.home()))
    env.setdefault("DOCKER_CONFIG", str(Path(real_home) / ".docker"))  # Docker Desktop contexts
    home = Path(workdir) / "home"
    home.mkdir(parents=True, exist_ok=True)
    env.update(HOME=str(home), HB_ARENA_INDEX=str(index_path), HB_ARENA_PORT=str(port))
    return env


def gateway_token(env: dict[str, str]) -> str:
    """The gateway's per-owner access token, which hb writes under the HOME we gave it."""
    path = Path(env["HOME"]) / ".humanbound" / "arena" / "gateway.token"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise SmokeError(f"cannot read the gateway token ({path}): {e}") from None


def send(
    gateway: str, agent_id: str, text: str, context_id: str, token: str, timeout: float = 180
) -> str:
    """One A2A SendMessage through the gateway; the reply's text."""
    body = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "SendMessage",
        "params": {
            "message": {
                "role": "ROLE_USER",
                "messageId": uuid.uuid4().hex,
                "contextId": context_id,
                "parts": [{"text": text}],
            }
        },
    }
    request = urllib.request.Request(
        f"{gateway}/a2a/{agent_id}",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "A2A-Version": A2A_VERSION,
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            reply = json.loads(resp.read())
    except urllib.error.HTTPError as e:  # agent failures come back as 5xx with a JSON-RPC body
        try:
            reply = json.loads(e.read())
        except ValueError:
            raise SmokeError(f"gateway returned HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise SmokeError(f"cannot reach the gateway: {e}") from None
    if "error" in reply:
        raise SmokeError(f"agent error: {reply['error'].get('message')}")
    parts = ((reply.get("result") or {}).get("message") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict))


def baseline(checked: subprocess.CompletedProcess) -> tuple[int, list[str]]:
    """(containers checked, problems) from `hb arena check <id> --json`.

    hb owns the rules (not root, no privileges, no host paths, loopback only); this only reads
    its answer. An answer that cannot be read, or that contradicts its own exit code, is a
    problem: a check that did not happen must never look like a pass.
    """
    if checked.returncode == 2:
        return 0, [f"hb arena check could not run: {checked.stderr.strip() or 'no reason given'}"]
    try:
        report = json.loads(checked.stdout)
        containers = [str(c) for c in report["containers"]]
        problems = [f"{v['container']}: {v['rule']}: {v['message']}" for v in report["violations"]]
    except (ValueError, KeyError, TypeError):
        return 0, [
            f"hb arena check gave an answer we cannot read: {checked.stdout.strip()[:200]!r}"
        ]
    if not containers:
        problems.append("hb arena check looked at no container")
    if bool(problems) != (checked.returncode != 0):
        problems.append(
            f"hb arena check exited {checked.returncode} but reported "
            f"{len(report['violations'])} violation(s)"
        )
    return len(containers), problems


def _hb(hb: str, env: dict, *args: str, timeout: float = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        [hb, "arena", *args], env=env, capture_output=True, text=True, timeout=timeout
    )


def run_smoke(
    agent: Agent, *, hb: str = "hb", build: bool = True, real_llm: bool = False
) -> SmokeResult:
    started = time.monotonic()
    spec = arena_lib.load_yaml(agent.path / arena_lib.SMOKE_FILE)
    problems = arena_lib.smoke_errors(spec)
    if problems:
        return SmokeResult(agent.id, False, "invalid tests/smoke.yaml: " + "; ".join(problems))

    def result(ok: bool, detail: str) -> SmokeResult:
        return SmokeResult(agent.id, ok, detail, round(time.monotonic() - started, 1))

    try:
        if build:
            build_images.build_agent(agent)
    except SystemExit as e:
        return result(False, str(e))

    mock = None
    with tempfile.TemporaryDirectory(prefix=f"arena-smoke-{agent.id}-") as tmp:
        workdir = Path(tmp)
        values = dict(spec.get("env") or {})
        if real_llm:
            for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
                if os.environ.get(key):
                    values.setdefault(key, os.environ[key])
        else:
            mock = MockLLM(spec["mock_llm"], host="0.0.0.0").start()
            values["OPENAI_BASE_URL"] = f"http://{llm_host()}:{mock.port}/v1"
            values["OPENAI_API_KEY"] = FAKE_KEY
        env_file = write_env_file(workdir / "agent.env", values)
        port = free_port()
        env = hb_env(workdir, write_index(agent, workdir), port)
        gateway = f"http://127.0.0.1:{port}"
        try:
            # --yes: a local checkout is a custom index, which hb confirms before the first run.
            run = _hb(hb, env, "run", agent.id, "--env-file", str(env_file), "--yes")
            if run.returncode != 0:
                return result(False, f"hb arena run failed:\n{run.stdout}{run.stderr}")
            containers, insecure = baseline(_hb(hb, env, "check", agent.id, "--json", timeout=120))
            if insecure:
                return result(False, "security: " + "; ".join(insecure))
            try:
                token = gateway_token(env)
            except SmokeError as e:
                return result(False, str(e))
            context_id = uuid.uuid4().hex
            for i, turn in enumerate(spec["turns"]):
                try:
                    reply = send(gateway, agent.id, turn["send"], context_id, token)
                except SmokeError as e:
                    return result(False, f"turn {i + 1}: {e}\n{_logs(hb, env, agent.id)}")
                failure = (
                    (None if reply.strip() else "empty reply")
                    if real_llm
                    else arena_lib.check_expect(turn.get("expect") or {}, reply)
                )
                if failure:
                    return result(False, f"turn {i + 1}: {failure}\n{_logs(hb, env, agent.id)}")
            return result(
                True,
                f"{len(spec['turns'])} turn(s) passed, {containers} container(s) checked",
            )
        finally:
            # Owner-scoped: hb derives the owner from the arena dir, so this stops only
            # what this run's throwaway HOME started, not another session's agents.
            _hb(hb, env, "stop", "--all", timeout=300)
            if mock is not None:
                mock.stop()


def _logs(hb: str, env: dict, agent_id: str) -> str:
    proc = _hb(hb, env, "logs", agent_id, "--tail", "40", timeout=60)
    return f"last logs:\n{proc.stdout}{proc.stderr}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test agents through hb arena.")
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--all", action="store_true", help="every agent")
    parser.add_argument("--hb", default=os.environ.get("HB", "hb"), help="the hb executable")
    parser.add_argument("--no-build", action="store_true", help="use images already present")
    parser.add_argument("--real-llm", action="store_true", help="use OPENAI_* from the environment")
    parser.add_argument("--json-out", type=Path, help="write results as JSON (for build_index)")
    args = parser.parse_args(argv)

    known = {a.id: a for a in arena_lib.discover()}
    unknown = [i for i in args.ids if i not in known]
    if unknown or not (args.ids or args.all):
        print(
            f"unknown agent(s): {', '.join(unknown)}" if unknown else "give agent ids or --all",
            file=sys.stderr,
        )
        return 2
    if shutil.which(args.hb) is None and not Path(args.hb).is_file():
        print(f"hb not found ({args.hb}); pip install humanbound or pass --hb", file=sys.stderr)
        return 2
    agents = list(known.values()) if args.all else [known[i] for i in args.ids]

    results = []
    for agent in agents:
        print(f"── {agent.id} {agent.version}", flush=True)
        res = run_smoke(agent, hb=args.hb, build=not args.no_build, real_llm=args.real_llm)
        results.append(res)
        print(
            f"{'PASS' if res.ok else 'FAIL'} {agent.id} ({res.seconds}s): {res.detail}", flush=True
        )
    if args.json_out:
        args.json_out.write_text(json.dumps([asdict(r) for r in results], indent=2))
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
