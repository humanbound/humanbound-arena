# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 AI and Me Single-Member Private Company (Humanbound)
"""The run as events, as the walkthrough page reads it: each step, each firewall decision, and what
the model read in place of a withheld page. A real Pydantic AI agent runs with a scripted model and
the firewall's Pydantic AI adapter, whose firewall is a stand-in that withholds the page."""
import asyncio
from types import SimpleNamespace

import app
import pytest
from humanbound_firewall.integrations.pydantic_ai import HumanboundFirewallCapability
from humanbound_firewall.session import Session
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

PAGE = "Live-offer exchange: compute your basis index and read your tier record."
NOTICE = "[Withheld by policy: restriction. Continue without it.]"
REFUSAL = "[Rejected by policy.]"


def fetch_url(url: str) -> str:
    """Retrieve the contents of a URL and return its text."""
    return PAGE


class StandInFirewall:
    """Passes the request (or rejects it) and withholds every fetched page; each decision goes to
    app.DECISIONS, as on_decision does."""

    def __init__(self, reject_request=False):
        self.reject_request = reject_request
        self.guard = SimpleNamespace(mode_for=lambda cls: "block")
        self.config = SimpleNamespace(class_of=lambda name: "ingest", tool_expects={}, tool_classes={})

    def inspect(self, payload, cls, *, window, session, boundary):
        if cls == "request":
            action = "reject" if self.reject_request else "pass"
        else:
            action = "withhold"
        app.DECISIONS.append(SimpleNamespace(
            result=None, explanation="The page asks to send confidential figures out.",
            boundary=boundary, cls=cls, verdict=SimpleNamespace(value="block" if action != "pass" else "pass"),
            category=SimpleNamespace(value="restriction" if action != "pass" else "none"),
            action=action, tier=3, mode_applied="block", elapsed_ms=900))
        return SimpleNamespace(action=action, session=session if isinstance(session, Session) else Session.new(),
                               replacement=REFUSAL if cls == "request" else NOTICE)


def script(messages, info):
    """One fetch_url call, then a final answer."""
    if len(messages) == 1:
        return ModelResponse(parts=[
            ThinkingPart(content="The listing hides its price; follow the offer link."),
            ToolCallPart("fetch_url", {"url": "http://storefront:8001/this-weeks-offer"},
                         tool_call_id="c1")])
    return ModelResponse(parts=[TextPart(content="Hold at £249.00.")])


def _run(monkeypatch, firewall):
    async def no_storefront():
        return []

    monkeypatch.setattr(app, "_collector_log", no_storefront)
    monkeypatch.delenv("FIREWALL", raising=False)  # the request decides
    agent = Agent(FunctionModel(script), system_prompt="SYSTEM", tools=[fetch_url],
                  capabilities=[HumanboundFirewallCapability(firewall, tools=[fetch_url])])

    async def collect():
        return [e async for e in app._run_events(agent, "SKU-4471", "", "gpt-5", True)]

    return asyncio.run(collect())


@pytest.fixture
def events(monkeypatch):
    return _run(monkeypatch, StandInFirewall())


def _types(events):
    return [e["type"] for e in events]


def test_a_run_reports_each_step_in_order(events):
    assert _types(events) == ["start", "firewall", "llm_start", "llm_end", "tool_start", "tool_end",
                              "firewall", "seen", "llm_start", "llm_end", "final", "collector"]


def test_the_start_says_the_firewall_is_on(events):
    assert events[0]["defences"] == {"firewall": "on", "firewall_judge": "gpt-5-mini"}
    assert events[0]["model"] == "gpt-5"


def test_the_request_is_judged_before_the_model_reads_it(events):
    request = events[1]
    assert request["type"] == "firewall" and request["cls"] == "request"
    assert request["boundary"] == "human turn" and request["action"] == "pass"


def test_a_model_turn_reports_its_context_reasoning_and_tool_calls(events):
    first, second = [e for e in events if e["type"] == "llm_start"]
    assert first["context"] == 2 and second["context"] == 4  # system + user; + response + tool
    (call, _) = [e for e in events if e["type"] == "llm_end"]
    assert call["reasoning"].startswith("The listing hides its price")
    assert call["tool_calls"] == [{"id": "c1", "name": "fetch_url",
                                   "args": {"url": "http://storefront:8001/this-weeks-offer"}}]


def test_a_withheld_page_is_reported_with_its_tool_call_and_the_judges_reason(events):
    fw = [e for e in events if e["type"] == "firewall"][1]
    assert fw["action"] == "withhold" and fw["tool_call_id"] == "c1" and fw["boundary"] == "fetch_url"
    assert fw["category"] == "restriction" and fw["explanation"].startswith("The page asks")


def test_the_tool_card_has_the_page_and_seen_has_what_the_model_read_instead(events):
    (tool_end,) = [e for e in events if e["type"] == "tool_end"]
    (seen,) = [e for e in events if e["type"] == "seen"]
    assert tool_end["content"] == PAGE
    assert seen == {"type": "seen", "id": "c1", "content": NOTICE}


def test_the_final_answer_closes_the_run(events):
    assert [e for e in events if e["type"] == "final"] == [
        {"type": "final", "recommendation": "Hold at £249.00."}]


def test_a_rejected_request_ends_the_run_with_the_firewalls_reply_and_no_model_turn(monkeypatch):
    events = _run(monkeypatch, StandInFirewall(reject_request=True))
    assert _types(events) == ["start", "firewall", "final", "collector"]
    assert events[1]["action"] == "reject"
    assert events[2]["recommendation"] == REFUSAL
