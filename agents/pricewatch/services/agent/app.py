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

import agent_core
import db
import httpx
from agent_core import build_agent, build_task, run_job
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

db.init_db()  # OPENAI_API_KEY is passed on the command line (no .env dependency)

STATE = {"agents": {}, "model": os.getenv("OPENAI_MODEL", "gpt-4o")}  # (model, firewall) -> agent
DECISIONS = []  # the firewall's decisions, as they are made (on_decision); a run drains them


def _firewall_on():
    return os.getenv("FIREWALL", "off").lower() == "on"


def _judge_model():
    return os.getenv("FIREWALL_JUDGE_MODEL", "gpt-4.1-mini")


def _agent(model, firewall):
    """The agent for a model, with or without the firewall, built once and kept."""
    key = (model, firewall)
    if key not in STATE["agents"]:
        os.environ["OPENAI_MODEL"] = model  # what get_llm reads, while this agent is built
        STATE["agents"][key] = build_agent(firewall=firewall, on_decision=DECISIONS.append)
    return STATE["agents"][key]


app = FastAPI(title="PriceWatch agent")


def _text(content):
    """Reasoning models (gpt-5 / o-series via the Responses API) return message content as a LIST of
    blocks, not a string. Flatten to plain text so the UI always gets a string recommendation."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text") or b.get("content") or "")
            else:
                parts.append(str(b))
        return " ".join(p for p in parts if p)
    return str(content)


def _reasoning_summary(m):
    """Pull a readable reasoning summary out of an AI message, if the model returned one."""
    ak = getattr(m, "additional_kwargs", None) or {}
    r = ak.get("reasoning")
    if isinstance(r, dict):  # Responses API shape: {"summary": [{"text": ...}, ...]}
        return "\n".join(s.get("text", "") for s in (r.get("summary") or []) if isinstance(s, dict))
    if isinstance(r, str):
        return r
    if ak.get("reasoning_content"):
        return str(ak["reasoning_content"])
    return ""


def _strip_encrypted(content):
    """Drop the opaque encrypted reasoning ciphertext that reasoning models (gpt-5 / o-series via
    the Responses API) return as `encrypted_content` on their content blocks. It is not readable
    evidence — the human-readable reasoning is captured separately as the reasoning summary — and
    leaving it in produces huge base64 blobs that also trip secret scanners. All readable text is
    kept; only the ciphertext field is removed."""
    if isinstance(content, list):
        return [{k: v for k, v in b.items() if k != "encrypted_content"} if isinstance(b, dict) else b
                for b in content]
    return content


def _trace(messages):
    steps, id_to_name = [], {}
    for m in messages:
        for tc in (getattr(m, "tool_calls", None) or []):
            if tc.get("id"):
                id_to_name[tc["id"]] = tc["name"]
            steps.append({"type": "tool_call", "name": tc["name"], "args": tc.get("args", {})})
        if isinstance(m, ToolMessage):
            steps.append({"type": "tool_result",
                          "name": id_to_name.get(getattr(m, "tool_call_id", ""), ""),
                          "content": str(m.content)})
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
        final = messages[-1]
        recommendation = _text(final.content)
    except Exception as e:
        print(f"[PriceWatch] agent error: {e}")
        return JSONResponse(status_code=500, content={"error": "agent error", "detail": str(e)})
    return JSONResponse({"sku": sku, "model": model, "firewall": "on" if firewall else "off",
                         "recommendation": recommendation, "trace": _trace(messages)})


# ============================ THE RUN, AS EVENTS ============================
# The same run as /run, reported as server-sent events: one per model turn and tool call, and one
# per firewall decision. It runs the same agent as /run and adds no behaviour.
_MAX_CHARS = 20000  # a tool result is shown whole in the UI; cap it so one huge page can't flood it


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
    seen, turn, final = set(), 0, None

    async def firewall_events():
        while decisions:
            d = decisions.pop(0)
            explanation = await asyncio.to_thread(d.wait_explanation, 10) if d.result else d.explanation
            payload = _text(d.result.prompt) if d.result else ""
            fe = {"type": "firewall", "boundary": (d.boundary or {}).get("name"), "cls": d.cls,
                  "tool_call_id": (d.boundary or {}).get("tool_call_id"),
                  "verdict": d.verdict.value, "category": d.category.value,
                  "action": d.action, "tier": d.tier, "mode": d.mode_applied,
                  "verdict_ms": d.elapsed_ms,
                  "letter": getattr(d.result, "raw_letter", "") if d.result else "",
                  "explanation": explanation, "input": payload[:_MAX_CHARS]}
            yield fe
    async for ev in agent.astream_events(
            {"messages": [SystemMessage(content=agent_core.SYSTEM_PROMPT), HumanMessage(content=task)]},
            version="v2"):
        kind, data = ev["event"], ev.get("data") or {}
        node = (ev.get("metadata") or {}).get("langgraph_node")
        if kind == "on_chat_model_start" and node == "model":
            # The tools node is done: report the firewall's decisions before the model's next turn.
            async for fe in firewall_events():
                yield fe
            context = (data.get("input") or {}).get("messages") or [[]]
            context = context[0] if context and isinstance(context[0], list) else context
            for m in context:
                if isinstance(m, ToolMessage) and m.tool_call_id not in seen:
                    seen.add(m.tool_call_id)
                    yield {"type": "seen", "id": m.tool_call_id,
                           "content": _text(m.content)[:_MAX_CHARS]}
            turn += 1
            yield {"type": "llm_start", "turn": turn, "context": len(context)}
        elif kind == "on_chat_model_end" and node == "model":
            final = out = data.get("output")
            yield {"type": "llm_end", "turn": turn,
                   "text": _text(_strip_encrypted(getattr(out, "content", ""))),
                   "reasoning": _reasoning_summary(out) if out is not None else "",
                   "tool_calls": [{"id": tc.get("id"), "name": tc["name"], "args": tc.get("args", {})}
                                  for tc in (getattr(out, "tool_calls", None) or [])]}
        elif kind == "on_tool_start":
            yield {"type": "tool_start", "name": ev.get("name"), "args": data.get("input")}
        elif kind == "on_tool_end":
            out = data.get("output")
            content = out.content if isinstance(out, ToolMessage) else out
            text = _text(content)
            yield {"type": "tool_end", "name": ev.get("name"),
                   "id": getattr(out, "tool_call_id", None),
                   "content": text[:_MAX_CHARS]}
            async for fe in firewall_events():
                yield fe
    async for fe in firewall_events():
        yield fe
    yield {"type": "final",
           "recommendation": _text(_strip_encrypted(getattr(final, "content", ""))) if final else ""}
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
