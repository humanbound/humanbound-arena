# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
import json
import urllib.request

import pytest
import yaml

from mock_llm import MockLLM, load_script


@pytest.fixture
def mock():
    servers = []

    def start(replies):
        server = MockLLM(replies).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.stop()


def _post(url, body):
    req = urllib.request.Request(
        url, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.headers.get("Content-Type"), resp.read().decode()


def _chat(server, **extra):
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}], **extra}
    return _post(f"{server.url}/chat/completions", body)


def test_replies_come_back_in_order_then_the_last_repeats(mock):
    server = mock([{"content": "one"}, {"content": "two"}])
    texts = [json.loads(_chat(server)[1])["choices"][0]["message"]["content"] for _ in range(3)]
    assert texts == ["one", "two", "two"]


def test_tool_call_reply(mock):
    server = mock([{"tool_calls": [{"name": "lookup", "arguments": {"sku": "A"}}]}])
    choice = json.loads(_chat(server)[1])["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    call = choice["message"]["tool_calls"][0]
    assert call["type"] == "function" and call["function"]["name"] == "lookup"
    assert json.loads(call["function"]["arguments"]) == {"sku": "A"}
    assert call["id"].startswith("call_")


def test_streaming_reply_reassembles_and_ends_with_done(mock):
    server = mock([{"content": "streamed text"}])
    content_type, raw = _chat(server, stream=True)
    assert content_type.startswith("text/event-stream")
    events = [line[len("data: ") :] for line in raw.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    chunks = [json.loads(e) for e in events[:-1]]
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c["choices"])
    assert text == "streamed text"


def test_streaming_tool_calls(mock):
    server = mock([{"tool_calls": [{"name": "lookup", "arguments": {"sku": "A"}}]}])
    _, raw = _chat(server, stream=True)
    chunks = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: {")]
    calls = [
        tc for c in chunks if c["choices"] for tc in c["choices"][0]["delta"].get("tool_calls", [])
    ]
    assert calls[0]["function"]["name"] == "lookup"
    finishes = [c["choices"][0].get("finish_reason") for c in chunks if c["choices"]]
    assert "tool_calls" in finishes


def test_responses_api_returns_output_text(mock):
    server = mock([{"content": "from responses"}])
    body = json.loads(_post(f"{server.url}/responses", {"model": "m", "input": "hi"})[1])
    assert body["output_text"] == "from responses"
    assert body["output"][0]["content"][0]["text"] == "from responses"


def test_models_and_request_log(mock):
    server = mock([{"content": "x"}])
    _chat(server)
    with urllib.request.urlopen(f"{server.url}/models", timeout=5) as resp:
        assert json.loads(resp.read())["data"][0]["id"] == "mock"
    base = server.url.removesuffix("/v1")
    with urllib.request.urlopen(f"{base}/_mock/requests", timeout=5) as resp:
        log = json.loads(resp.read())
    assert log[0]["messages"][0]["content"] == "hi"
    assert server.requests == log


def test_unknown_path_is_404(mock):
    server = mock([{"content": "x"}])
    with pytest.raises(urllib.error.HTTPError) as err:
        _post(f"{server.url}/nope", {})
    assert err.value.code == 404


def test_load_script_reads_mock_llm_from_a_smoke_file_or_a_plain_list(tmp_path):
    smoke = tmp_path / "smoke.yaml"
    smoke.write_text(yaml.safe_dump({"mock_llm": [{"content": "a"}], "turns": []}))
    assert load_script(smoke) == [{"content": "a"}]
    plain = tmp_path / "replies.json"
    plain.write_text(json.dumps([{"content": "b"}]))
    assert load_script(plain) == [{"content": "b"}]
