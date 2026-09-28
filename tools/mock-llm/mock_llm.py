# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
"""A scripted, OpenAI-compatible LLM for smoke tests.

It answers every chat request with the next reply from a script, in order, and keeps
repeating the last reply once the script runs out (agents may retry or make extra calls).
A reply is {"content": "..."} and/or {"tool_calls": [{"name": ..., "arguments": {...}}]}.

Standard library only (PyYAML is needed just to read a .yaml script), so it runs anywhere:

    python tools/mock-llm/mock_llm.py --script agents/hello-world/tests/smoke.yaml

Endpoints: POST /v1/chat/completions (plain or `stream: true`), POST /v1/responses
(content only), GET /v1/models, GET /health, GET /_mock/requests (every request body seen).
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL = "mock"


def load_script(path: str | Path) -> list[dict]:
    """Replies from a smoke.yaml (its `mock_llm` list), or a JSON/YAML list of replies."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        doc = json.loads(text)
    else:
        import yaml

        doc = yaml.safe_load(text)
    if isinstance(doc, dict):
        doc = doc.get("mock_llm")
    if not isinstance(doc, list) or not doc:
        raise ValueError(f"{path}: expected a non-empty list of replies (or a mock_llm key)")
    return doc


def _tool_calls(reply: dict) -> list[dict]:
    return [
        {
            "id": f"call_{i}",
            "type": "function",
            "function": {
                "name": call["name"],
                "arguments": json.dumps(call.get("arguments") or {}),
            },
        }
        for i, call in enumerate(reply.get("tool_calls") or [])
    ]


def _usage() -> dict:
    return {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}


def completion(reply: dict, model: str) -> dict:
    message: dict = {"role": "assistant", "content": reply.get("content")}
    calls = _tool_calls(reply)
    if calls:
        message["tool_calls"] = calls
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if calls else "stop",
            }
        ],
        "usage": _usage(),
    }


def completion_chunks(reply: dict, model: str) -> list[dict]:
    base = {
        "id": "chatcmpl-mock",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }
    calls = _tool_calls(reply)
    chunks = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant"}}]}]
    if reply.get("content"):
        delta = {"content": reply["content"]}
        chunks.append({**base, "choices": [{"index": 0, "delta": delta}]})
    if calls:
        delta = {"tool_calls": [{"index": i, **c} for i, c in enumerate(calls)]}
        chunks.append({**base, "choices": [{"index": 0, "delta": delta}]})
    finish = "tool_calls" if calls else "stop"
    chunks.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    chunks.append({**base, "choices": [], "usage": _usage()})
    return chunks


def response(reply: dict, model: str) -> dict:
    text = reply.get("content") or ""
    return {
        "id": "resp-mock",
        "object": "response",
        "model": model,
        "status": "completed",
        "output": [
            {
                "type": "message",
                "id": "msg-mock",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "output_text": text,
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


class MockLLM:
    def __init__(self, replies: list[dict], host: str = "127.0.0.1", port: int = 0):
        if not replies:
            raise ValueError("the mock LLM needs at least one scripted reply")
        self.replies = list(replies)
        self.requests: list[dict] = []
        self._next = 0
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer((host, port), self._handler())
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        return self._server.server_address[0]

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host == "0.0.0.0" else self.host
        return f"http://{host}:{self.port}/v1"

    def next_reply(self, body: dict) -> dict:
        with self._lock:
            self.requests.append(body)
            reply = self.replies[min(self._next, len(self.replies) - 1)]
            self._next += 1
            return reply

    def start(self) -> MockLLM:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def _handler(self):
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/health":
                    return self._send(200, {"status": "ok"})
                if self.path == "/v1/models":
                    return self._send(
                        200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]}
                    )
                if self.path == "/_mock/requests":
                    with mock._lock:
                        return self._send(200, list(mock.requests))
                self._send(404, {"error": {"message": f"no route {self.path}"}})

            def do_POST(self):
                if self.path not in ("/v1/chat/completions", "/v1/responses"):
                    return self._send(404, {"error": {"message": f"no route {self.path}"}})
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    return self._send(400, {"error": {"message": "invalid JSON"}})
                reply = mock.next_reply(body)
                model = body.get("model") or MODEL
                if self.path == "/v1/responses":
                    return self._send(200, response(reply, model))
                if body.get("stream"):
                    return self._stream(completion_chunks(reply, model))
                self._send(200, completion(reply, model))

            def _send(self, status, data):
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _stream(self, chunks):
                raw = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
                data = raw.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--script", required=True, help="smoke.yaml, or a JSON/YAML reply list")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args(argv)
    server = MockLLM(load_script(args.script), args.host, args.port)
    print(f"mock LLM on http://{args.host}:{server.port}/v1 ({len(server.replies)} replies)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
