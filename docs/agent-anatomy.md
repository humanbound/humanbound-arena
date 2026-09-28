# Agent anatomy

What an arena agent's folder holds, and every field of its `arena.yaml`. [`agents/hello-world`](../agents/hello-world/) is the reference: copy it to start ([how](adding-an-agent.md#3-start-from-hello-world)).

## The agent folder

An agent's folder holds two things: the **deployment** (what gets built and run) and the **lab** (what teaches). Nothing else may sit in it.

```
agents/<id>/
├── arena.yaml
├── README.md
├── .dockerignore
├── docker-compose.yml        only with several services
├── services/
│   ├── agent/                the service hb talks to
│   │   ├── Dockerfile
│   │   └── ...
│   └── <other>/              any other service the scenario needs
├── tests/
│   ├── smoke.yaml
│   └── test_*.py
└── lab/
    ├── README.md
    └── ...
```

| Path | Required | What it is |
|---|---|---|
| `arena.yaml` | yes | The manifest (below). Its `id` equals the folder name. |
| `README.md` | yes | The front page, under its [fixed headings](#the-pages). No spoilers. An agent built on someone else's code also credits the upstream, and says what we changed and why. |
| `services/agent/Dockerfile` | yes | The service hb talks to. The build context is the agent folder, so a Dockerfile writes `COPY services/agent/... /app/` and can also copy `arena.yaml`. |
| `tests/smoke.yaml` | yes | The plumbing check CI runs against a scripted mock LLM ([below](#testssmokeyaml)). |
| `lab/README.md` | yes | The hands-on walkthrough, under its [fixed headings](#the-pages). An hour or less. |
| `.dockerignore` | recommended | Keeps `lab/` and `tests/` out of the images. |
| `services/<other>/Dockerfile` + `docker-compose.yml` | several services | One folder per extra service (a fake storefront, a mailbox, an attacker's collector). |
| `tests/test_*.py` | optional | Unit tests of the services. |
| `lab/**` | optional | Anything else the agent has to teach: a course, its own FAQ, a benchmark, a demo page, helper scripts. Setup problems common to every agent go in the shared [troubleshooting page](troubleshooting.md). |
| `DEPRECATED` | optional | Its presence marks the agent `deprecated` in `index.json` (write the reason inside). |

`services/` holds only what a running service needs to answer a request. A page for people, a debugging aid, study tooling or example credentials all belong in `lab/`.

Limits for every file: 1 MB at most, and text only, except web images (`.png .jpg .jpeg .gif .webp .ico`). No `.env` files.

## The pages

`README.md` and `lab/README.md` carry the same headings in every agent, in the same order, so someone who has done one lab knows their way around the next. A page may number its headings and add its own after them.

**`README.md`**, the front page:

| Heading | What goes under it |
|---|---|
| *(title)* | The [safety notice](#the-safety-notice), then the story in a paragraph or two. |
| `## What you'll learn` | The outcomes, the difficulty and the time it takes. |
| `## Quickstart` | `hb arena run <id>`, then `hb test --target arena://<id>`. Nothing is run by hand. |
| `## How it's built` | The services and how a request moves through them. |

**`lab/README.md`**, the walkthrough. Each heading answers one question:

| Heading | The question it answers |
|---|---|
| *(title)* | The [safety notice](#the-safety-notice) and a spoiler notice, then the time, the difficulty and the outcomes. |
| `## Set up` | How do I get it running? |
| `## Understand the target` | How does it work, and where does it trust something it shouldn't? |
| `## Attack` | How do I break it by hand? |
| `## Test` | How do I find the same thing with `hb test`? |
| `## Defend` | Why does it happen, and what fixes it? |
| `## Clean up` | How do I stop it? |

What is specific to an agent goes one level down, as sub-headings in whatever wording suits the story. Under `## Attack`, **each vulnerability in `ground_truth` is named in a heading** by its label:

```markdown
## Attack

### Leak the secret (V1, prompt injection)
### The hidden instruction that leaks the cost (V2, V3)
```

Every attack section has the same three parts: **the attack**, as commands to paste, with how often it worked when you measured it; **Evidence**, what to look for in the reply; and **Why it works**. Quote rates you measured, not certainties.

One section may cover several labels, when the vulnerabilities form one chain. The lab studies the vulnerabilities the agent declares; a test run may report others, which are outside what the agent covers ([ground truth](ground-truth.md)).

### The safety notice

Both pages open with the same warning, before their first section, word for word. Only the link changes with the page's depth:

```markdown
> ⚠️ **Deliberately vulnerable. Use at your own risk.** We isolate every agent as well as we can, but no isolation is complete. Run it only where there is no sensitive data and no access to critical systems. See [SECURITY.md](../../SECURITY.md).
```

It is checked, so a reworded or missing notice fails the pull request. The full statement is in [SECURITY.md](../SECURITY.md#use-at-your-own-risk).

## Dockerfiles

`hb` takes privileges away and limits resources when it starts a container. Two things are the image's own, so every `services/*/Dockerfile` must:

| Rule | Why |
|---|---|
| **End as an unprivileged user.** The last `USER` of the final stage is not `root` or `0`. | Without a `USER` line a container runs as root. `hb` does not choose the user; the image does. |
| **Name the version it is built on.** `FROM` carries a tag other than `latest`, or a digest. | `FROM python` builds on whatever was published last, so the same file gives a different image next month. |

```dockerfile
FROM python:3.12-slim
COPY services/agent/app.py /app/app.py
USER 65534
CMD ["python", "/app/app.py"]
```

Root is fine while building (to install packages, say), as long as the image drops it before it ends. A compose agent that uses someone else's image, one that runs as root, sets `user:` for that service in its `docker-compose.yml`. A later stage may build on an earlier one (`FROM build`).

## Images

The repository builds and publishes every image; the names are fixed:

| Agent kind | Built from | Image |
|---|---|---|
| single service (`source.image`) | `services/agent/Dockerfile` | `ghcr.io/humanbound/arena-<id>:<version>` |
| several services (`source.compose`) | `services/<svc>/Dockerfile` for each of our services | `ghcr.io/humanbound/arena-<id>-<svc>:<version>` |

Tags are immutable, so every change to an agent needs a version bump. A compose file references images only (no `build:`), because `hb` downloads just the compose file. Third-party images in it (a database, say) must be pinned to a tag other than `latest`, or a digest.

A compose file must pass `hb`'s allowlist: no published ports (hb publishes the `agent` service on `127.0.0.1` itself), no privileged mode, host namespaces, `cap_add`, bind mounts, devices, `env_file` or remote build contexts, and no `io.humanbound.*` labels. `hb arena validate` checks it.

## `arena.yaml` reference

`hb arena validate agents/<id>/arena.yaml` is the authority on the schema; `python scripts/policy_check.py <id>` adds the repository's own rules (marked **repo** below).

### Identity

| Field | Type | Notes |
|---|---|---|
| `schema_version` | int | `1` |
| `id` | string | `^[a-z0-9][a-z0-9-]{0,62}$`; **repo:** equals the folder name |
| `name` | string | Display name |
| `version` | string | `X.Y.Z`; bump on every change |
| `developer` | `{name, url, contact?}` | **repo:** `name` and an http(s) `url` are required; `contact` (an email or issue tracker) is optional. Descriptive credit — no tooling branches on it. |
| `description` | string | One spoiler-free sentence; shown by `hb arena ls` and in the catalog |
| `tags` | list of strings | Free-form, e.g. `[indirect-injection, tools]` |
| `difficulty` | `easy` \| `medium` \| `hard` | |

### `agent:`, the embedded agent.yaml

The Humanbound `agent.yaml`, unchanged: `hb test` uses it as the agent's scope, and `hb arena info <id> --agent-yaml` prints it. Extra keys (`name`, `tools`, `settings`, ...) are kept, so an agent can use the same block as its own firewall policy.

```yaml
agent:
  version: "1.0"
  scope:
    business: What the agent is for, in the words of the business that runs it.
    more_info: "HIGH: what makes it sensitive"
  intents:
    permitted: [things it should do]
    restricted: [things it must never do]
  capabilities: [tools]         # optional; hb's capability keys
```

To make the judge catch a planted vulnerability, encode it as a **restricted intent** here — the judge is driven by scope and intents, not by `ground_truth`.

### `source:`

| Field | Notes |
|---|---|
| `image` | Single-service agents: `ghcr.io/humanbound/arena-<id>:<version>` |
| `compose` + `service` | Multi-service agents: the compose file (`docker-compose.yml`) and the service hb talks to, **repo:** always `agent` |
| `upstream: {repo, ref, license}` | Optional, for an agent built on someone else's code: the upstream repository, the full 40-character commit SHA the Dockerfile checks out, and an SPDX id or expression (`MIT OR Apache-2.0`); every licence named must be on the OSI allowlist in `scripts/policy_check.py`. Validated when present. |

Exactly one of `image` or `compose`.

### Image pins live in the catalog, not here

The author writes a readable tag (`ghcr.io/humanbound/arena-<id>:<version>`) in `source.image` or a compose file, and never a digest — a digest doesn't exist until CI pushes the image on merge, so requiring one in `arena.yaml` would mean no agent could be added in a single pull request. CI records each pushed image's digest in `index.json` as an optional `images: [{ref, digest}]`; when a digest is missing there, `hb` pulls by tag.

### `runtime:`

| Field | Default | Notes |
|---|---|---|
| `port` | | The port the `agent` service listens on inside its container |
| `health: {path, timeout_s}` | `/health`, 60 | hb waits for a 2xx here before serving the agent |
| `env: {required, optional}` | | Env var names hb passes to the agent. None may start with `HB_` or `HUMANBOUND_`. **repo:** `OPENAI_BASE_URL` must be declared, and the agent must honour it |
| `timeout_s` | 120 | How long the gateway waits for one reply |
| `egress` | none | Hosts the agent may reach besides its model and its own services ([below](#what-an-agent-may-reach)) |

Keys come from `hb arena config set`, `--env-file`, and (agents from the default Humanbound catalog only) the user's shell.

### What an agent may reach

`hb` starts every agent on a network with no way out, and runs a door beside it: the only way in from the user's machine, and the only way out. The agent gets `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY` pointing at it. There is no switch to turn this off.

| The agent may reach | How |
|---|---|
| Its model | The host and port of `OPENAI_BASE_URL`, or `api.openai.com:443` when it is unset. A model on the user's machine works as `host.docker.internal:<port>`, that port only. |
| Its own services | Directly, by name. |
| Hosts in `runtime.egress` | Through the door. |

```yaml
runtime:
  egress:
    - files.example.com          # port 443
    - api.example.com:8443
```

Up to 20 exact host names, lower case, each with an optional port. No wildcards, IP addresses or URLs, and no name of the user's own machine or network. Leave `egress` out unless the agent cannot work without it: every entry is a way out that a tricked agent can use.

What this asks of an agent:

- **Its HTTP client honours the proxy variables.** Most libraries do by default. A client that ignores them cannot connect at all.
- **It needs nothing from outside to start or to run** beyond the list above: no package installs, telemetry, fonts or CDN files. Put what it needs in the image.
- **It does not declare `HTTP_PROXY`, `HTTPS_PROXY` or `NO_PROXY`** in `runtime.env`, and no compose service is named `hb-door`. `hb` owns both.

A refused request gets `403` with the header `X-Arena-Egress: blocked`. `hb arena logs <id> --door` lists every destination the agent tried.

### `integration:`

How the gateway talks to the agent's native API.

```yaml
integration:
  type: http                        # or a2a (the agent speaks A2A itself, at `path`)
  thread_init:                      # optional: a call that opens a conversation
    endpoint: /threads
    payload: {}
  chat_completion:
    endpoint: /chat                 # may use $<key> from thread_init's reply
    headers: {}
    payload: { prompt: "$PROMPT", history: "$CONVERSATION" }   # $PROMPT is required
  response:
    text: reply                     # dotted path to the reply text
    tool_calls: trace.tools         # optional: dotted path to the tool calls
  history: gateway                  # none | gateway (hb sends $CONVERSATION) | agent (it keeps its own)
```

### Tool calls, telemetry and test depth

`hb test` reports **whitebox** depth only when `integration.type` is `http` **and** `response.tool_calls` is set; otherwise the run is **blackbox**. A tool-less conversational agent (like hello-world) has no `response.tool_calls` and runs blackbox, which is correct — there is nothing to report.

For an agent with tools, `response.tool_calls` is what turns its tool activity into evidence the LLM judge can reason about. The chain:

1. The gateway reads the list at `response.tool_calls` and **normalises** each entry to `{name, parameters, result}`, keeping the original under `tool_calls_raw`. Aliases are accepted and equally fine — `name` ← `name`/`tool`/`tool_name`, `parameters` ← `parameters`/`args`/`arguments`/`input`, `result` ← `result`/`output`/`content` — and a `type: call` entry is merged with the `type: result` that follows it. **An agent does not need to change its native shape.**
2. It attaches that to every reply as per-turn telemetry at `result.message.metadata.humanbound` (alongside `latency_ms`, `agent`, `version`).
3. `hb`'s engine reads it via the arena's generated bot config (`telemetry.mode: per_turn`, `extraction_map.metadata_path`) and standardises it to `tool_executions: [{turn, tool_name, parameters, result}]`. The standardiser's **default field names are exactly `name`, `parameters`, `result`**, which is why the gateway normalises to those.
4. The judge renders that into its prompt as a `## TELEMETRY DATA` section — `Turn 3: fetch_url (params: {...}) → Result: ...` — which is what lifts the verdict from blackbox to whitebox.

The local engine and the hosted backend share this schema and the same standardiser, so telemetry that works locally works hosted, unchanged.

**What the judge actually sees.** It renders only three of the standardised arrays: `tool_executions`, `memory_operations` and `resource_usage`. The arena fills **`tool_executions` only**; `memory_operations`, `external_calls`, `resource_usage`, `authorization_events` and `agent_delegation` stay empty, and `tool_calls_raw` is for humans and debugging — it is not forwarded to the judge.

**Extending telemetry (not author-configurable yet).** The `extraction_map` is built by the CLI (`humanbound_cli/arena/target.py`), not read from `arena.yaml`, so an agent cannot add telemetry channels from its manifest today. Surfacing e.g. memory writes or egress to the judge needs a CLI change in two places: the gateway must put the extra list in its metadata, and the arena bot config must map it (`"memory_operations": "<key>"`, with optional per-field overrides like `"memory_operations.operation_type"`). Until then, encode anything the judge must weigh as a **restricted intent** in the `agent:` block, and use `ground_truth.success` `tool_called` conditions for machine-checkable evidence.

### `context` and `ground_truth`

- `context`: up to 1500 characters describing the fictional setting, what is secret, and what counts as a finding. `hb test` gives it to the attacker as well as the judge, so **describe secrets, never quote them** ("a staff-only discount code", not the code itself): an attacker that already knows the secret makes its leak untestable. **repo:** a `reply_contains` evidence string may not appear in `context`.
- `ground_truth`: every planted vulnerability. See [ground-truth.md](ground-truth.md).

## `tests/smoke.yaml`

```yaml
mock_llm:                           # scripted model replies, returned in order (the last repeats)
  - content: "Recommended price for SKU-4471: $129"
  - tool_calls: [{ name: lookup, arguments: { sku: SKU-4471 } }]
env:                                # optional: extra env for the agent (must be declared in runtime.env)
  OPENAI_MODEL: gpt-4o-mini
turns:                              # sent through the gateway, in one conversation
  - send: "What should SKU-4471 cost?"
    expect: { reply_contains: "SKU-4471" }    # or reply_regex
```

`python scripts/smoke.py <id>` builds the images, starts the mock LLM, runs `hb arena run <id>` with `OPENAI_BASE_URL` pointing at the mock, sends the turns through the gateway, and stops everything. It proves the plumbing, not the vulnerability.

Before it sends anything, it asks `hb arena check <id>` whether every container of the agent, side services included, meets the container baseline. It fails if one:

- runs as root, or is privileged;
- keeps or adds Linux capabilities, or may gain new privileges;
- shares a namespace with the host or with another container, or mounts a host path or device;
- publishes a port anywhere but `127.0.0.1`;
- is on a network with a way out.

`hb` runs the same check on the user's machine each time an agent starts, and again before `hb test` uses it, and stops an agent that fails. The rules live in `hb`, so CI and the user's machine apply the same ones.

The check is about how a container is set up. It says nothing about what the agent does.
