# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
"""Builders for throwaway arena repositories used across the script tests."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

SMOKE = {
    "mock_llm": [{"content": "Hello from the mock"}],
    "turns": [{"send": "Hi", "expect": {"reply_contains": "mock"}}],
}


DEVELOPER = {"name": "Humanbound team", "url": "https://github.com/humanbound"}


def manifest(agent_id: str = "demo", **overrides) -> dict:
    """A manifest that passes both hb arena validate and the policy check."""
    doc = {
        "schema_version": 1,
        "id": agent_id,
        "name": agent_id.title(),
        "version": "1.0.0",
        "developer": dict(DEVELOPER),
        "description": f"The {agent_id} agent.",
        "tags": ["demo"],
        "difficulty": "easy",
        "agent": {
            "version": "1.0",
            "scope": {"business": "Answers questions.", "more_info": "LOW"},
            "intents": {"permitted": ["Answer"], "restricted": ["Reveal the code"]},
        },
        "source": {"image": f"ghcr.io/humanbound/arena-{agent_id}:1.0.0"},
        "runtime": {
            "port": 8080,
            "env": {"required": ["OPENAI_API_KEY"], "optional": ["OPENAI_BASE_URL"]},
        },
        "integration": {
            "type": "http",
            "chat_completion": {"endpoint": "/chat", "payload": {"prompt": "$PROMPT"}},
            "response": {"text": "reply"},
        },
        "ground_truth": {
            "V1": {
                "category": "llm001",
                "title": "Direct injection",
                "description": "Reveals the code.",
                "severity": "medium",
                "vector": "direct",
                "success": [{"reply_contains": "CODE"}],
                "references": ["atlas:AML.T0051.000", "atlas:AML.T0057"],
            }
        },
    }
    for key, value in overrides.items():
        doc[key] = value
    return doc


DOCKERFILE = "FROM python:3.12-slim\nUSER 65534\n"

NOTICE = (
    "> ⚠️ **Deliberately vulnerable. Use at your own risk.** We isolate every agent as well as we "
    "can, but no isolation is complete. Run it only where there is no sensitive data and no "
    "access to critical systems. See [SECURITY.md](../../SECURITY.md)."
)

README = f"""# Demo

{NOTICE}

## What you'll learn

## Quickstart

## How it's built
"""


def lab(doc: dict) -> str:
    """A lab with the six headings, and a sub-heading under Attack per declared vulnerability."""
    attacks = [f"### Break it ({key})" for key in (doc.get("ground_truth") or {})]
    parts = [
        "# Lab",
        NOTICE,
        "## Set up",
        "## Understand the target",
        "## Attack",
        *attacks,
        "## Test",
        "## Defend",
        "## Clean up",
    ]
    return "\n\n".join(parts) + "\n"


def make_agent(
    root: Path,
    agent_id: str = "demo",
    doc: dict | None = None,
    files: dict[str, str | bytes] | None = None,
    smoke: dict | None = None,
) -> Path:
    """Write an agent folder with the files every valid agent needs."""
    path = root / "agents" / agent_id
    path.mkdir(parents=True, exist_ok=True)
    doc = doc if doc is not None else manifest(agent_id)
    defaults: dict[str, str | bytes] = {
        "arena.yaml": yaml.safe_dump(doc, sort_keys=False),
        "README.md": README,
        "lab/README.md": lab(doc),
        "services/agent/Dockerfile": DOCKERFILE,
        "tests/smoke.yaml": yaml.safe_dump(smoke if smoke is not None else copy.deepcopy(SMOKE)),
    }
    for rel, content in {**defaults, **(files or {})}.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content)
    return path


@pytest.fixture
def repo(tmp_path) -> Path:
    (tmp_path / "agents").mkdir()
    return tmp_path
