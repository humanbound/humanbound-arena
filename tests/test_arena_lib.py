# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
import yaml

import arena_lib
from conftest import SMOKE, make_agent, manifest


def test_discover_finds_agents_sorted_and_skips_folders_without_a_manifest(repo):
    make_agent(repo, agent_id="shop")
    make_agent(repo, agent_id="alpha")
    (repo / "agents" / "not-an-agent").mkdir()
    agents = arena_lib.discover(repo)
    assert [(a.id, a.version, a.rel_path) for a in agents] == [
        ("alpha", "1.0.0", "agents/alpha"),
        ("shop", "1.0.0", "agents/shop"),
    ]


def test_agent_exposes_the_developer_block(repo):
    make_agent(repo, agent_id="shop")
    agent = arena_lib.find_agent("shop", repo)
    assert agent.developer == {
        "name": "Humanbound team",
        "url": "https://github.com/humanbound",
    }
    assert not hasattr(agent, "origin")


def test_find_agent_by_id(repo):
    make_agent(repo, agent_id="demo")
    assert arena_lib.find_agent("demo", repo).path == repo / "agents" / "demo"


def test_image_builds_for_an_image_agent(repo):
    path = make_agent(repo)
    (build,) = arena_lib.image_builds(arena_lib.find_agent("demo", repo))
    assert build == arena_lib.ImageBuild(
        "ghcr.io/humanbound/arena-demo:1.0.0", path, path / "services" / "agent" / "Dockerfile"
    )


def test_image_builds_for_a_compose_agent_skip_third_party_images(repo):
    doc = manifest("shop", source={"compose": "docker-compose.yml", "service": "agent"})
    compose = {
        "services": {
            "agent": {"image": "ghcr.io/humanbound/arena-shop-agent:1.0.0"},
            "db": {"image": "postgres:16"},
        }
    }
    path = make_agent(
        repo, agent_id="shop", doc=doc, files={"docker-compose.yml": yaml.safe_dump(compose)}
    )
    builds = arena_lib.image_builds(arena_lib.find_agent("shop", repo))
    assert builds == [
        arena_lib.ImageBuild(
            "ghcr.io/humanbound/arena-shop-agent:1.0.0",
            path,
            path / "services" / "agent" / "Dockerfile",
        )
    ]


def test_expected_image_names():
    assert arena_lib.expected_image("x", "1.2.3") == "ghcr.io/humanbound/arena-x:1.2.3"
    assert arena_lib.expected_image("x", "1.2.3", "web") == "ghcr.io/humanbound/arena-x-web:1.2.3"


def test_smoke_errors_accepts_the_documented_shape():
    doc = {
        "mock_llm": [
            {"content": "Recommended price: $129"},
            {"tool_calls": [{"name": "lookup", "arguments": {"sku": "A"}}]},
        ],
        "env": {"OPENAI_MODEL": "mock"},
        "turns": [
            {"send": "hi", "expect": {"reply_contains": "129"}},
            {"send": "again", "expect": {"reply_regex": "\\$\\d+"}},
            {"send": "no expectation"},
        ],
    }
    assert arena_lib.smoke_errors(doc) == []


def test_smoke_errors_reports_each_problem():
    assert arena_lib.smoke_errors("nope") == ["tests/smoke.yaml must be a mapping"]
    assert any("turns" in e for e in arena_lib.smoke_errors({"mock_llm": SMOKE["mock_llm"]}))
    assert any("mock_llm" in e for e in arena_lib.smoke_errors({**SMOKE, "mock_llm": []}))
    assert any("content" in e for e in arena_lib.smoke_errors({**SMOKE, "mock_llm": [{}]}))
    bad_expect = {**SMOKE, "turns": [{"send": "x", "expect": {"reply_is": "y"}}]}
    assert any("reply_is" in e for e in arena_lib.smoke_errors(bad_expect))
    bad_regex = {**SMOKE, "turns": [{"send": "x", "expect": {"reply_regex": "("}}]}
    assert any("regex" in e for e in arena_lib.smoke_errors(bad_regex))
    bad_env = {**SMOKE, "env": {"A": 1}}
    assert any("env" in e for e in arena_lib.smoke_errors(bad_env))


def test_success_errors():
    ok = [
        {"reply_contains": "x"},
        {"reply_regex": "a+b"},
        {"tool_called": {"name": "fetch_url", "arg_regex": "v=\\d"}},
        {"tool_called": {"name": "fetch_url"}},
    ]
    assert arena_lib.success_errors(ok) == []
    assert arena_lib.success_errors([]) == ["success must be a non-empty list"]
    assert any("unknown" in e for e in arena_lib.success_errors([{"egress_contains": "x"}]))
    two = [{"reply_contains": "x", "reply_regex": "y"}]
    assert any("exactly one" in e for e in arena_lib.success_errors(two))
    assert any("name" in e for e in arena_lib.success_errors([{"tool_called": {}}]))
    bad = [{"tool_called": {"name": "f", "arg_regex": "("}}]
    assert any("regex" in e for e in arena_lib.success_errors(bad))


def test_check_expect():
    assert arena_lib.check_expect({"reply_contains": "x"}, "axb") is None
    assert arena_lib.check_expect({"reply_regex": "\\d{3}"}, "cost 129") is None
    assert "reply_contains" in arena_lib.check_expect({"reply_contains": "zz"}, "abc")
    assert "reply_regex" in arena_lib.check_expect({"reply_regex": "\\d"}, "abc")
    assert arena_lib.check_expect({}, "anything") is None
