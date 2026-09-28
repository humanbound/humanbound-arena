# Adding an agent

How a Humanbound maintainer takes an agent from idea to catalog. We do not accept pull requests from outside the maintainers for now; to suggest an agent, [open an issue](https://github.com/humanbound/humanbound-arena/issues/new?template=suggest-an-agent.yml) ([why](../CONTRIBUTING.md)).

Read the [admission policy](admission-policy.md) first; [`agents/hello-world`](../agents/hello-world/) is the reference to follow.

## 1. Agree what it teaches

Write down what is vulnerable, what it teaches and how the damage is contained, and agree it with another maintainer before building. It saves building something that duplicates an existing agent or can't be contained.

## 2. Set up

```bash
git clone https://github.com/humanbound/humanbound-arena && cd humanbound-arena
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pip install humanbound   # the hb CLI, including hb arena
```

You also need Docker (Docker Desktop, or Docker Engine with Compose v2).

## 3. Start from hello-world

Your own agent starts as a copy of the reference agent:

```bash
cp -R agents/hello-world agents/<id>
```

The folder name is the agent's id. Then make it yours:

- `arena.yaml`: `id`, `name`, `description`, `tags`, `difficulty`, the image name (`ghcr.io/humanbound/arena-<id>:1.0.0`), `developer`, the `agent:` scope, `context` and `ground_truth`;
- `services/agent/`: the agent itself (any language or framework; keep `GET /health` and the endpoint `integration` describes);
- `README.md` and `lab/README.md`: your story and your lab, under the [fixed headings](agent-anatomy.md#the-pages);
- `tests/smoke.yaml`: scripted replies and turns that fit your agent.

An agent built from someone else's code also starts from `agents/hello-world`. Add `source.upstream: {repo, ref, license}` to `arena.yaml`, pinning `ref` to the full commit SHA you're building from, and have `services/agent/Dockerfile` fetch the upstream at that same pinned commit.

## 4. Build the agent

- Put the service hb talks to in `services/agent/`, with its `Dockerfile`. The build context is the agent folder, so `COPY services/agent/... /app/`.
- Need more services (a fake website, a mailbox, an attacker's collector)? Add `services/<name>/Dockerfile` for each, a `docker-compose.yml` that references their images (`ghcr.io/humanbound/arena-<id>-<name>:<version>`, no `build:`, no `ports:`), and switch `source` to `{compose: docker-compose.yml, service: agent}`.
- Read the LLM settings from `OPENAI_BASE_URL`, `OPENAI_API_KEY` and `OPENAI_MODEL`, and declare them in `runtime.env`.
- Keep all data fictional and baked into the image.
- Declare every planted vulnerability in `ground_truth` ([guide](ground-truth.md)), and give each a matching restricted intent so the judge catches it.

Field reference: [agent-anatomy.md](agent-anatomy.md).

## 5. Check it

```bash
hb arena validate agents/<id>/arena.yaml     # the manifest schema and the compose allowlist
python scripts/policy_check.py <id>          # the repository's rules
python scripts/smoke.py <id>                 # build, run through hb arena with the mock LLM, check the containers, stop
```

Then try it for real, the way users will, against a local catalog that contains your agent:

```bash
python scripts/build_images.py <id>          # build the images locally, with their real tags
python scripts/build_index.py                # regenerates index.json (don't commit this change)
HB_ARENA_INDEX=$PWD hb arena run <id> --yes   # --yes: a local checkout is a custom catalog
HB_ARENA_INDEX=$PWD hb test --target arena://<id>
git checkout index.json README.md
```

## 6. Write the learning material

Every agent needs a `lab/README.md`, an hour or less, under the [fixed headings](agent-anatomy.md#the-pages): how to set up, one section for each vulnerability in `ground_truth` with the working attack step by step, then `hb test --target arena://<id>`, why it happens and how to fix it, and how to clean up. Quote success rates you measured, not certainties.

Anything else the agent has to teach (a course, its own FAQ, a benchmark, a demo page) also goes in `lab/`. Setup problems common to every agent belong in the shared [troubleshooting page](troubleshooting.md), not in the agent's.

## 7. Open the pull request

Sign off every commit (`git commit -s`) and fill in the template. Write a readable image tag in `source.image` or your compose file (`ghcr.io/humanbound/arena-<id>:<version>`) — never a digest, since none exists until CI pushes the image. Don't edit `index.json`: once the pull request merges, CI publishes the images and opens a second pull request, the catalog update, with the regenerated `index.json`. The agent appears in the catalog when that one is merged.

## Changing an existing agent

Bump its `version` in `arena.yaml` (and in the image names in `source.image` or `docker-compose.yml`): image tags are immutable. Changing planted vulnerabilities is a minor bump, or a major one if it breaks the lab.
