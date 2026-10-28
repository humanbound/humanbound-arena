# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 AI and Me Single-Member Private Company (Humanbound)
"""app.py — the PriceWatch agent's HTTP service.

Two services: this agent (:8000) and the competitor storefront (:8001). Ask it to reprice a SKU and
it chases the door-opener and exfiltrates the unit cost to the attacker's collector. The walkthrough
page that draws a run step by step is teaching material and lives in lab/ui/, not here.

The agent runs unprotected unless FIREWALL=on: then the Humanbound firewall judges the request and
every tool result before the model reads it, and withholds what breaks the policy. Every decision is
reported to the walkthrough, with the judge's reason.

For the lab, a run request may also say "firewall": "on" or "off", to show the attack with and without
the firewall on the same running agent. A real deployment would not let a request switch its firewall
off; hb test's requests never carry it, so they get the FIREWALL setting.

The REAL agent is agent_core.py + tools.py. THIS file is only the HTTP shell around it.
If you want to play with or extend the attack, work in agent_core/tools.

Endpoints:
  POST /run          invoke the agent for one SKU -> recommendation (+ a debug trace)
  POST /run/stream   the SAME run as server-sent events (one per model turn / tool call), for a
                     client that shows the run as it happens — no new agent behaviour
  GET  /health       liveness / status
"""
import asyncio
import json
import os

import db
import httpx
from agent_core import build_agent, build_task, run_job
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)

db.init_db()  # OPENAI_API_KEY is passed on the command line (no .env dependency)

STATE = {"agents": {}, "model": os.getenv("OPENAI_MODEL", "gpt-5")}  # (model, firewall) -> agent
DECISIONS = []  # the firewall's decisions, as they are made (on_decision); a run drains them


def _firewall_on():
    return os.getenv("FIREWALL", "off").lower() == "on"


def _judge_model():
    return os.getenv("FIREWALL_JUDGE_MODEL", "gpt-5-mini")


def _agent(model, firewall):
    """The agent for a model, with or without the firewall, built once and kept."""
    key = (model, firewall)
    if key not in STATE["agents"]:
        os.environ["OPENAI_MODEL"] = model  # what get_llm reads, while this agent is built
        STATE["agents"][key] = build_agent(firewall=firewall, on_decision=DECISIONS.append)
    return STATE["agents"][key]


app = FastAPI(title="PriceWatch agent")


def _text(parts):
    """The text of a model response: its text parts, joined. (Its reasoning is in its thinking
    parts, read separately by _reasoning_summary.)"""
    return " ".join(p.content for p in parts if isinstance(p, TextPart) and p.content)


def _final(messages):
    """The agent's reply: the text of the last model response."""
    for m in reversed(messages):
        if isinstance(m, ModelResponse):
            return _text(m.parts)
    return ""


def _reasoning_summary(response):
    """A readable reasoning summary from a model response, if the model returned one (gpt-5 /
    o-series via the Responses API with summary:auto). The opaque encrypted reasoning a thinking
    part also carries (its signature) is not readable evidence and is left out."""
    return "\n".join(p.content for p in response.parts if isinstance(p, ThinkingPart) and p.content)


def _tool_calls(response):
    return [p for p in response.parts if isinstance(p, ToolCallPart)]


def _trace(messages):
    steps = []
    for m in messages:
        for p in m.parts:
            if isinstance(p, ToolCallPart):
                steps.append({"type": "tool_call", "name": p.tool_name, "args": p.args_as_dict()})
            elif isinstance(p, ToolReturnPart):
                steps.append({"type": "tool_result", "name": p.tool_name,
                              "content": p.model_response_str()})
    return steps


# ============================ CORE AGENT ============================
# Shared request plumbing + the agent's real endpoint. In a production deployment this (plus
# agent_core/tools) is essentially all you would ship.
def _configure(body):
    """The settings a run request carries, for this run only: (sku, question, model, firewall). A
    request that does not choose gets the agent's own: OPENAI_MODEL and FIREWALL."""
    model = body.get("model") or STATE["model"]
    choice = str(body.get("firewall") or "").lower()
    firewall = choice == "on" if choice in ("on", "off") else _firewall_on()
    return body.get("sku") or "SKU-4471", (body.get("question") or "").strip(), model, firewall


# The agent's endpoint: run the agent on one SKU and return its recommendation. The `trace`
# it also returns is a debug convenience (the full tool-call list); the recommendation is the product.
@app.post("/run")
async def run(request: Request):
    body = await request.json() if await request.body() else {}
    sku, question, model, firewall = _configure(body)
    try:
        messages = await run_job(_agent(model, firewall), sku, question)
        recommendation = _final(messages)
    except Exception as e:
        print(f"[PriceWatch] agent error: {e}")
        return JSONResponse(status_code=500, content={"error": "agent error", "detail": str(e)})
    return JSONResponse({"sku": sku, "model": model, "firewall": "on" if firewall else "off",
                         "recommendation": recommendation, "trace": _trace(messages)})


# ============================ THE RUN, AS EVENTS ============================
# The same run as /run, reported as server-sent events: one per model turn and tool call, and one
# per firewall decision. It runs the same agent as /run and adds no behaviour.
_MAX_CHARS = 20000  # a tool result is shown whole in the UI; cap it so one huge page can't flood it


class RawToolOutputs(AbstractCapability):
    """Records what each tool returned, before anything else sees it (it is the innermost layer,
    inside the firewall), so the walkthrough can show the raw page beside what the model read. It
    observes only: every result is passed on unchanged."""

    def __init__(self, outputs):
        self.outputs = outputs

    def get_ordering(self):
        return CapabilityOrdering(position="innermost")

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        result = await handler(args)
        self.outputs[call.tool_call_id] = result if isinstance(result, str) else str(result)
        return result


def _context_size(messages):
    """How many messages the model has in context, counted as a chat API counts them: each
    system prompt, user turn and tool result is one, and each model response is one."""
    return sum(1 if isinstance(m, ModelResponse) else len(m.parts) for m in messages)


async def _collector_log():
    """What the attacker's collector has received so far (None if the storefront is not running)."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            return (await client.get(f"{db._BASE}/collect/log")).json()
    except Exception:
        return None


async def _run_events(agent, sku, question, model, firewall):
    """One run as a sequence of events: every model turn (its context size, its text, reasoning
    summary and tool calls) and every tool call (its input and its raw output). What the model
    actually received from each tool is reported separately ("seen"), read from the context of the
    next model turn — it differs from the raw output when the firewall withheld it.

    With the firewall on, every Decision (which boundary, what it judged, verdict, category, the
    judge's explanation, the `action` taken, and the time to verdict) is a "firewall" event."""
    task = build_task(sku, question)
    secrets = (db.query_products(sku=sku, columns=["cost", "floor", "margin"]) or [{}])[0]
    decisions = DECISIONS
    decisions.clear()
    yield {"type": "start", "sku": sku, "task": task, "model": model,
           "defences": {"firewall": "on" if firewall else "off",
                        "firewall_judge": _judge_model() if firewall else None},
           "secrets": secrets}
    before = await _collector_log()
    raw, turn, rejected = {}, 0, False

    async def firewall_events():
        nonlocal rejected
        while decisions:
            d = decisions.pop(0)
            explanation = await asyncio.to_thread(d.wait_explanation, 10) if d.result else d.explanation
            payload = (d.result.prompt or "") if d.result else ""
            rejected = rejected or (d.cls == "request" and d.action == "reject")
            fe = {"type": "firewall", "boundary": (d.boundary or {}).get("name"), "cls": d.cls,
                  "tool_call_id": (d.boundary or {}).get("tool_call_id"),
                  "verdict": d.verdict.value, "category": d.category.value,
                  "action": d.action, "tier": d.tier, "mode": d.mode_applied,
                  "verdict_ms": d.elapsed_ms,
                  "letter": getattr(d.result, "raw_letter", "") if d.result else "",
                  "explanation": explanation, "input": payload[:_MAX_CHARS]}
            yield fe
    async with agent.iter(task, capabilities=[RawToolOutputs(raw)]) as run:
        async for node in run:
            # The previous step is done: report the firewall's decisions before the next one.
            async for fe in firewall_events():
                yield fe
            if Agent.is_model_request_node(node):
                for p in node.request.parts:  # what the model reads from each tool, firewall applied
                    if isinstance(p, ToolReturnPart):
                        yield {"type": "seen", "id": p.tool_call_id,
                               "content": p.model_response_str()[:_MAX_CHARS]}
                if rejected:  # a rejected request: the model is not called, the firewall replies
                    continue
                turn += 1
                yield {"type": "llm_start", "turn": turn,
                       "context": _context_size(run.all_messages()) + len(node.request.parts)}
            elif Agent.is_call_tools_node(node):
                out = node.model_response
                if not rejected:
                    yield {"type": "llm_end", "turn": turn, "text": _text(out.parts),
                           "reasoning": _reasoning_summary(out),
                           "tool_calls": [{"id": c.tool_call_id, "name": c.tool_name,
                                           "args": c.args_as_dict()} for c in _tool_calls(out)]}
                async with node.stream(run.ctx) as stream:
                    async for ev in stream:
                        if isinstance(ev, FunctionToolCallEvent):
                            yield {"type": "tool_start", "name": ev.part.tool_name,
                                   "args": ev.part.args_as_dict()}
                        elif isinstance(ev, FunctionToolResultEvent):
                            r = ev.part
                            text = raw.get(r.tool_call_id, r.model_response_str())
                            yield {"type": "tool_end", "name": r.tool_name, "id": r.tool_call_id,
                                   "content": text[:_MAX_CHARS]}
                            async for fe in firewall_events():
                                yield fe
    async for fe in firewall_events():
        yield fe
    yield {"type": "final", "recommendation": run.result.output if run.result else ""}
    after = await _collector_log()
    yield {"type": "collector", "available": after is not None,
           "entries": (after or [])[len(before or []):]}


# The same run as /run, streamed as SSE.
@app.post("/run/stream")
async def run_stream(request: Request):
    """The run, streamed as server-sent events so a client can show each step as it happens."""
    body = await request.json() if await request.body() else {}
    sku, question, model, firewall = _configure(body)

    async def sse():
        try:
            async for event in _run_events(_agent(model, firewall), sku, question, model, firewall):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as e:
            print(f"[PriceWatch] agent error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'detail': str(e)})}\n\n"
        yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# Liveness / status.
@app.get("/health")
async def health():
    return {"status": "ok", "agent": "PriceWatch", "model": STATE["model"],
            "defences": {"firewall": "on" if _firewall_on() else "off",
                         "firewall_judge": _judge_model() if _firewall_on() else None}}
