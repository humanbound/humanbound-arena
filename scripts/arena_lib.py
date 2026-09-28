# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
"""Shared pieces for the repository scripts: finding agents, their images, and smoke specs.

Manifests are handled as plain YAML mappings here. `hb arena validate` is the authority on the
arena.yaml schema; these scripts only add the repository's own rules on top of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
# Every agent lives here. Who wrote one is described by its manifest's `developer` block.
AGENTS_DIR = "agents"
GHCR = "ghcr.io/humanbound"
OUR_IMAGE_PREFIX = f"{GHCR}/arena-"
MAX_FILE_BYTES = 1_000_000
SMOKE_FILE = "tests/smoke.yaml"
COMPOSE_FILE = "docker-compose.yml"
# Every agent keeps the service hb talks to in services/agent/; more services sit beside it.
AGENT_SERVICE = "agent"

SUCCESS_KINDS = ("reply_contains", "reply_regex", "tool_called")
EXPECT_KINDS = ("reply_contains", "reply_regex")


@dataclass(frozen=True)
class Agent:
    id: str  # the folder name; policy_check makes sure arena.yaml says the same
    path: Path
    manifest: dict

    @property
    def version(self) -> str:
        return str(self.manifest.get("version", ""))

    @property
    def developer(self) -> dict:
        """Who wrote the agent: {name, url, contact?}. Descriptive only — nothing branches on it."""
        block = self.manifest.get("developer")
        return block if isinstance(block, dict) else {}

    @property
    def rel_path(self) -> str:
        return f"{AGENTS_DIR}/{self.id}"


def load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_agent(path: Path) -> Agent:
    """The agent in `path` (a folder holding arena.yaml). Raises ValueError if unreadable."""
    path = Path(path)
    try:
        doc = load_yaml(path / "arena.yaml")
    except (OSError, yaml.YAMLError, UnicodeDecodeError) as e:
        raise ValueError(f"cannot read {path / 'arena.yaml'}: {e}") from None
    if not isinstance(doc, dict):
        raise ValueError(f"{path / 'arena.yaml'} must be a YAML mapping")
    return Agent(path.name, path, doc)


def discover(root: Path = REPO_ROOT) -> list[Agent]:
    """Every agent under agents/, sorted by id."""
    base = Path(root) / AGENTS_DIR
    if not base.is_dir():
        return []
    agents = [load_agent(m.parent) for m in sorted(base.glob("*/arena.yaml"))]
    return sorted(agents, key=lambda a: a.id)


def find_agent(agent_id: str, root: Path = REPO_ROOT) -> Agent:
    for agent in discover(root):
        if agent.id == agent_id:
            return agent
    raise KeyError(f"no agent '{agent_id}' under {AGENTS_DIR}/")


def expected_image(agent_id: str, version: str, service: str | None = None) -> str:
    name = f"arena-{agent_id}-{service}" if service else f"arena-{agent_id}"
    return f"{GHCR}/{name}:{version}"


@dataclass(frozen=True)
class ImageBuild:
    tag: str
    context: Path  # always the agent folder, so a service can COPY arena.yaml
    dockerfile: Path


def load_compose(agent: Agent) -> dict:
    compose = (agent.manifest.get("source") or {}).get("compose") or COMPOSE_FILE
    doc = load_yaml(agent.path / compose)
    return doc if isinstance(doc, dict) else {}


def image_builds(agent: Agent) -> list[ImageBuild]:
    """The images this repository builds for an agent.

    The build context is always the agent folder. Image agents build services/agent/Dockerfile.
    Compose agents build services/<service>/Dockerfile for every service whose image is one of
    ours; third-party images (a database, say) are pulled as they are.
    """
    source = agent.manifest.get("source") or {}
    if source.get("image"):
        dockerfile = agent.path / "services" / AGENT_SERVICE / "Dockerfile"
        return [ImageBuild(source["image"], agent.path, dockerfile)]
    builds = []
    services = load_compose(agent).get("services") or {}
    for name, service in services.items():
        image = (service or {}).get("image") if isinstance(service, dict) else None
        if isinstance(image, str) and image.startswith(OUR_IMAGE_PREFIX):
            builds.append(
                ImageBuild(image, agent.path, agent.path / "services" / str(name) / "Dockerfile")
            )
    return builds


def _regex_error(value: object, where: str) -> str | None:
    if not isinstance(value, str) or not value:
        return f"{where} must be a non-empty string"
    try:
        re.compile(value)
    except re.error as e:
        return f"{where} is not a valid regex: {e}"
    return None


def success_errors(conditions: object) -> list[str]:
    """Problems with a ground_truth entry's `success` list (empty when it is valid)."""
    if not isinstance(conditions, list) or not conditions:
        return ["success must be a non-empty list"]
    errors = []
    for i, cond in enumerate(conditions):
        where = f"success[{i}]"
        if not isinstance(cond, dict) or len(cond) != 1:
            errors.append(f"{where} must have exactly one condition kind")
            continue
        ((kind, value),) = cond.items()
        if kind not in SUCCESS_KINDS:
            errors.append(f"{where}: unknown kind '{kind}' (known: {', '.join(SUCCESS_KINDS)})")
        elif kind == "reply_contains":
            if not isinstance(value, str) or not value:
                errors.append(f"{where}.reply_contains must be a non-empty string")
        elif kind == "reply_regex":
            if err := _regex_error(value, f"{where}.reply_regex"):
                errors.append(err)
        else:  # tool_called
            if not isinstance(value, dict) or not isinstance(value.get("name"), str):
                errors.append(f"{where}.tool_called needs a 'name'")
                continue
            unknown = set(value) - {"name", "arg_regex"}
            if unknown:
                errors.append(f"{where}.tool_called: unknown key(s) {', '.join(sorted(unknown))}")
            if "arg_regex" in value:
                err = _regex_error(value["arg_regex"], f"{where}.tool_called.arg_regex")
                if err:
                    errors.append(err)
    return errors


def smoke_errors(doc: object) -> list[str]:
    """Problems with a tests/smoke.yaml document (empty when it is valid)."""
    if not isinstance(doc, dict):
        return [f"{SMOKE_FILE} must be a mapping"]
    errors = []
    unknown = set(doc) - {"mock_llm", "env", "turns"}
    if unknown:
        errors.append(f"unknown key(s): {', '.join(sorted(unknown))}")

    replies = doc.get("mock_llm")
    if not isinstance(replies, list) or not replies:
        errors.append("mock_llm must be a non-empty list of scripted replies")
    else:
        for i, reply in enumerate(replies):
            where = f"mock_llm[{i}]"
            if not isinstance(reply, dict) or not (reply.get("content") or reply.get("tool_calls")):
                errors.append(f"{where} needs 'content' or 'tool_calls'")
                continue
            if "content" in reply and not isinstance(reply["content"], str):
                errors.append(f"{where}.content must be a string")
            for j, call in enumerate(reply.get("tool_calls") or []):
                if not isinstance(call, dict) or not isinstance(call.get("name"), str):
                    errors.append(f"{where}.tool_calls[{j}] needs a 'name'")
                elif not isinstance(call.get("arguments", {}), dict):
                    errors.append(f"{where}.tool_calls[{j}].arguments must be a mapping")

    env = doc.get("env", {})
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        errors.append("env must map names to string values")

    turns = doc.get("turns")
    if not isinstance(turns, list) or not turns:
        errors.append("turns must be a non-empty list")
    else:
        for i, turn in enumerate(turns):
            where = f"turns[{i}]"
            if not isinstance(turn, dict) or not isinstance(turn.get("send"), str):
                errors.append(f"{where} needs a 'send' string")
                continue
            expect = turn.get("expect", {})
            if not isinstance(expect, dict):
                errors.append(f"{where}.expect must be a mapping")
                continue
            for kind, value in expect.items():
                if kind not in EXPECT_KINDS:
                    errors.append(f"{where}.expect: unknown rule '{kind}'")
                elif kind == "reply_regex":
                    if err := _regex_error(value, f"{where}.expect.reply_regex"):
                        errors.append(err)
                elif not isinstance(value, str):
                    errors.append(f"{where}.expect.{kind} must be a string")
    return errors


def check_expect(expect: dict, reply: str) -> str | None:
    """None when `reply` meets every rule in `expect`, else a message naming the failed rule."""
    for kind, value in (expect or {}).items():
        if kind == "reply_contains" and value not in reply:
            return f"reply_contains {value!r} not found in reply: {reply[:300]!r}"
        if kind == "reply_regex" and not re.search(value, reply):
            return f"reply_regex {value!r} did not match reply: {reply[:300]!r}"
    return None
