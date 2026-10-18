# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 AI and Me Single-Member Private Company (Humanbound)
"""The Humanbound firewall, wired into the LangChain agent the way its README shows, off by default.

These tests never call a model or the network: create_agent and get_llm are replaced, and building
the firewall makes no network call (the judge is only called on first evaluation).
"""
import json
import re
import time
from types import SimpleNamespace

import agent_core
import pytest
import yaml

pytest.importorskip("humanbound_firewall")

from humanbound_firewall.llm.models import ProviderIntegration  # noqa: E402

_HAS_REASONING_EFFORT = "reasoning_effort" in ProviderIntegration.model_fields


@pytest.fixture
def built(monkeypatch):
    """Capture what build_agent hands to create_agent."""
    captured = {}

    def fake_create_agent(model, tools, **kwargs):
        captured.update(model=model, tools=tools, **kwargs)
        return "agent"

    monkeypatch.setattr(agent_core, "create_agent", fake_create_agent)
    monkeypatch.setattr(agent_core, "get_llm", lambda: "llm")
    monkeypatch.delenv("FIREWALL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    return captured


def _rows(middleware):
    return {b["name"]: b for b in middleware.boundaries}


def test_the_firewall_is_off_by_default(built):
    agent_core.build_agent()
    assert built["middleware"] == []
    assert len(built["tools"]) == 3


def test_firewall_on_in_the_environment_adds_the_langchain_middleware(built, monkeypatch):
    from humanbound_firewall.integrations.langchain import HumanboundFirewallMiddleware

    monkeypatch.setenv("FIREWALL", "on")
    agent_core.build_agent()
    (middleware,) = built["middleware"]
    assert isinstance(middleware, HumanboundFirewallMiddleware)


def test_a_caller_can_switch_it_on_without_the_environment(built):
    agent_core.build_agent(firewall=True)
    assert len(built["middleware"]) == 1


def test_it_judges_the_request_and_every_tool_and_enforces_them_all(built):
    agent_core.build_agent(firewall=True)
    rows = _rows(built["middleware"][0])
    assert rows["human turn"]["class"] == "request"
    assert rows["fetch_url"]["class"] == "ingest"
    assert rows["query_catalogue"]["class"] == "recall"  # our own records: an integrity check
    assert rows["check_our_stock"]["class"] == "recall"
    assert {r["mode"] for r in rows.values()} == {"block"}


def test_it_fails_closed_and_reports_every_decision(built, monkeypatch):
    logged, seen = [], []
    monkeypatch.setattr(agent_core, "log_decision", logged.append)
    agent_core.build_agent(firewall=True, on_decision=seen.append)
    guard = built["middleware"][0].firewall.guard
    assert guard.fail == "closed"  # HIGH-STAKE: a page the judge is unsure about is withheld
    guard.on_decision("a decision")
    assert logged == seen == ["a decision"]  # logged, and passed on


# --- the judge -------------------------------------------------------------------


@pytest.fixture
def from_config(monkeypatch):
    """Capture what build_firewall asks Firewall.from_config for."""
    seen = {}

    def fake(path, **kwargs):
        seen.update(path=path, **kwargs)
        return SimpleNamespace(_streamer=None)

    monkeypatch.setattr(agent_core.Firewall, "from_config", staticmethod(fake))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("FIREWALL_JUDGE_MODEL", raising=False)
    return seen


def test_the_firewall_reads_the_policy_file_next_to_the_agent(from_config):
    agent_core.build_firewall()
    assert from_config["path"] == agent_core.POLICY
    assert agent_core.POLICY.name == "agent.yaml" and agent_core.POLICY.is_file()


def test_the_judge_is_openai_with_the_agents_key(from_config):
    agent_core.build_firewall()
    provider = from_config["provider"]
    assert provider.name.value == "openai"
    assert provider.integration.api_key == "sk-test"
    assert provider.integration.model == "gpt-5-mini"


def test_the_judge_model_can_be_chosen_from_the_environment(from_config, monkeypatch):
    monkeypatch.setenv("FIREWALL_JUDGE_MODEL", "gpt-6-sol")
    agent_core.build_firewall()
    assert from_config["provider"].integration.model == "gpt-6-sol"


@pytest.mark.skipif(
    not _HAS_REASONING_EFFORT,
    reason="installed humanbound-firewall has no ProviderIntegration.reasoning_effort",
)
def test_the_judge_defaults_to_minimal_reasoning_effort(from_config, monkeypatch):
    monkeypatch.delenv("FIREWALL_JUDGE_REASONING_EFFORT", raising=False)
    agent_core.build_firewall()
    assert from_config["provider"].integration.reasoning_effort == "minimal"


@pytest.mark.skipif(
    not _HAS_REASONING_EFFORT,
    reason="installed humanbound-firewall has no ProviderIntegration.reasoning_effort",
)
def test_the_judge_reasoning_effort_comes_from_the_environment(from_config, monkeypatch):
    monkeypatch.setenv("FIREWALL_JUDGE_REASONING_EFFORT", "low")
    agent_core.build_firewall()
    assert from_config["provider"].integration.reasoning_effort == "low"


def test_without_a_key_the_firewall_says_so(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(KeyError, match="OPENAI_API_KEY"):
        agent_core.build_firewall()


# --- the firewall's log --------------------------------------------------------------


def _lines(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_the_firewall_logs_its_judge(built):
    assert isinstance(agent_core.build_firewall()._streamer, agent_core.JudgeLog)


def test_every_call_to_the_judge_is_logged_with_its_prompts_and_reply(capsys):
    class Judge:
        model = "gpt-5-mini"

        def ping(self, system_p, user_p, **kwargs):
            yield from ("C", " Sends our cost out.")

    log = agent_core.JudgeLog(Judge())
    assert "".join(log.ping("SYSTEM", "USER", max_tokens=5)) == "C Sends our cost out."
    assert _lines(capsys) == [{"firewall": "judge", "model": "gpt-5-mini", "system": "SYSTEM",
                               "user": "USER", "reply": "C Sends our cost out."}]


def test_a_judge_call_that_fails_is_logged_with_the_error(capsys):
    class Down:
        def ping(self, system_p, user_p, **kwargs):
            raise ConnectionError("no route")

    with pytest.raises(ConnectionError):
        agent_core.JudgeLog(Down()).ping("SYSTEM", "USER")
    (line,) = _lines(capsys)
    assert line["error"] == "ConnectionError('no route')" and line["user"] == "USER"


def test_every_decision_is_logged_once_its_reason_is_complete(capsys):
    result = SimpleNamespace(wait_explanation=lambda timeout: "Sends our cost out.",
                             raw_letter="C", prompt="the attacker's page")
    d = SimpleNamespace(result=result, boundary={"name": "fetch_url"}, cls="ingest", action="withhold",
                        verdict=SimpleNamespace(value="block"),
                        category=SimpleNamespace(value="restriction"), tier=3, elapsed_ms=900)
    agent_core.log_decision(d)
    out = ""
    for _ in range(200):  # the line is written off the agent's path
        out += capsys.readouterr().out
        if out:
            break
        time.sleep(0.01)
    assert json.loads(out) == {"firewall": "decision", "boundary": "fetch_url", "class": "ingest",
                               "action": "withhold", "verdict": "block", "category": "restriction",
                               "letter": "C", "tier": 3, "ms": 900, "reason": "Sends our cost out.",
                               "payload": "the attacker's page"}


# --- the policy file: the only PriceWatch-specific part of the firewall setup ---


def _policy():
    return yaml.safe_load(agent_core.POLICY.read_text())


def test_policy_declares_scope_and_both_intent_lists():
    policy = _policy()
    assert policy["scope"]["business"]
    assert policy["intents"]["permitted"]
    assert policy["intents"]["restricted"]


def test_policy_states_its_stakes_the_documented_way():
    """Risk is declared as a stakes marker in scope.more_info, the documented form the Tier 3
    judge reads: "HIGH-STAKE: ..."."""
    assert re.search(r"\bHIGH-STAKES?\b", _policy()["scope"]["more_info"])


def test_policy_declares_our_own_record_tools_as_recall():
    """Tools that return records WE authored are integrity-checked, not judged as outside content.
    Everything else stays ingest by default."""
    policy = _policy()
    assert policy["capabilities"] == ["tools"]
    assert sorted(policy["tools"]["recall"]) == ["check_our_stock", "query_catalogue"]
    assert "fetch_url" in policy["tools"]["expects"]


def test_policy_is_written_without_knowledge_of_the_attack():
    """The defense may know OUR system (what is confidential), never the attack's vocabulary,
    endpoint, or encoding."""
    text = agent_core.POLICY.read_text().lower()
    for attack_term in ("collect", "calibrat", "verification", "acquisition basis", "interleav",
                        "live-offer", "exchange", "127.0.0.1"):
        assert attack_term not in text


# --- the web app -----------------------------------------------------------------


@pytest.fixture
def fresh_app(built, monkeypatch):
    import app

    monkeypatch.setitem(app.STATE, "agents", {})
    return app


def test_the_app_reports_every_decision_to_the_walkthrough(built, fresh_app, monkeypatch):
    monkeypatch.setattr(agent_core, "log_decision", lambda d: None)
    fresh_app.DECISIONS.clear()
    fresh_app._agent("gpt-5", True)
    built["middleware"][0].firewall.guard.on_decision("a decision")
    assert fresh_app.DECISIONS == ["a decision"]


@pytest.mark.parametrize("env, body, expected", [
    (None, {}, False), ("on", {}, True),                      # no choice in the request: FIREWALL
    (None, {"firewall": "on"}, True), ("on", {"firewall": "off"}, False),
    ("on", {"firewall": "maybe"}, True)])                     # anything else: FIREWALL
def test_a_run_request_may_choose_the_firewall(monkeypatch, env, body, expected):
    import app

    if env is None:
        monkeypatch.delenv("FIREWALL", raising=False)
    else:
        monkeypatch.setenv("FIREWALL", env)
    assert app._configure(body)[3] is expected


def test_the_protected_and_the_unprotected_agent_are_built_once_each(fresh_app, monkeypatch):
    builds = []
    monkeypatch.setattr(fresh_app, "build_agent", lambda **kw: builds.append(kw["firewall"]) or object())
    on, off = fresh_app._agent("gpt-5", True), fresh_app._agent("gpt-5", False)
    assert on is not off
    assert fresh_app._agent("gpt-5", True) is on and fresh_app._agent("gpt-5", False) is off
    assert builds == [True, False]


@pytest.mark.parametrize("value, state", [(None, "off"), ("on", "on"), ("ON", "on"), ("yes", "off")])
def test_health_says_whether_the_firewall_is_on(monkeypatch, value, state):
    import app
    from fastapi.testclient import TestClient

    if value is None:
        monkeypatch.delenv("FIREWALL", raising=False)
    else:
        monkeypatch.setenv("FIREWALL", value)
    health = TestClient(app.app).get("/health").json()
    assert health["defences"]["firewall"] == state
    assert (health["defences"]["firewall_judge"] is not None) == (state == "on")


def test_a_model_chosen_by_a_request_is_for_that_run_only(monkeypatch):
    """The walkthrough page's model picker must not change the model hb test then gets."""
    import app

    monkeypatch.setitem(app.STATE, "model", "gpt-5")
    assert app._configure({"model": "gpt-5-nano"})[2] == "gpt-5-nano"
    assert app._configure({})[2] == "gpt-5" and app.STATE["model"] == "gpt-5"
