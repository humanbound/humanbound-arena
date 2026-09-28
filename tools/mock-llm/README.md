# mock-llm

A tiny OpenAI-compatible server that answers with scripted replies, in order. Smoke tests use it so that CI needs no API key and every run is deterministic. Standard library only; PyYAML is needed just to read a `.yaml` script.

```bash
python tools/mock-llm/mock_llm.py --script agents/hello-world/tests/smoke.yaml --port 9999
OPENAI_BASE_URL=http://127.0.0.1:9999/v1 OPENAI_API_KEY=sk-arena-FAKE-mock python your_agent.py
```

- The script is the `mock_llm` list of a `tests/smoke.yaml`, or a JSON/YAML list of replies.
- A reply is `{content: "..."}`, `{tool_calls: [{name, arguments}]}`, or both.
- Replies are returned in order; once the script runs out, the last reply repeats.
- Endpoints: `POST /v1/chat/completions` (including `stream: true`), `POST /v1/responses` (text only), `GET /v1/models`, `GET /health`, and `GET /_mock/requests`, which lists every request body received (handy when an agent misbehaves).

From an agent in Docker, reach a mock on your machine at `http://host.docker.internal:<port>/v1` (Docker Desktop), or at your host's IP on Linux, and start the mock with `--host 0.0.0.0`. `scripts/smoke.py` does all of this for you.
