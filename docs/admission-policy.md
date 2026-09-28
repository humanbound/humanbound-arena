# Admission policy

What an agent must be to join the arena, how it is reviewed, and how it leaves. The rules marked **(automated)** are checked by `scripts/policy_check.py` and `hb arena validate` in CI; the rest are checked by a maintainer.

## 6.1 One set of rules

Every agent lives in `agents/`, whether it's written from scratch or built on someone else's code, and every agent meets the same rules:

- **Code.** Lives here, under Apache-2.0. An agent built on someone else's code declares that in `source.upstream {repo, ref, license}` — the upstream repository, pinned to a full commit SHA the Dockerfile checks out, and its SPDX license id or expression (every licence named must be on the OSI allowlist) — instead of vendoring it.
- **The agent folder.** Holds the deployment (`services/`, `tests/`) and the teaching material (`lab/`), and nothing else ([layout](agent-anatomy.md#the-agent-folder)).
- **`lab/README.md`.** Required, with a section for each vulnerability in `ground_truth`. `README.md` and `lab/README.md` carry the [fixed headings](agent-anatomy.md#the-pages).
- **`ground_truth`.** Every field, including `success`, for every entry.
- **`developer`.** Descriptive credit for who wrote the agent: `name` and an http(s) `url` are required, `contact` (an email or issue tracker) is optional. No tooling branches on it.
- **Maintainer.** Humanbound.
- **Keys from the user's shell.** Only for agents from the default Humanbound catalog — the CLI decides from the index an agent's catalog was fetched from, not from anything the manifest declares. Any other catalog needs `--yes` on first run and never reads the shell.

Every agent uses the [same layout](agent-anatomy.md#the-agent-folder) **(automated)**.

## 6.2 Hard rules

Every rule must hold.

1. **Contained damage.** Planted vulnerabilities only affect services inside the agent's own compose project: fake storefronts, fake mailboxes, attacker collectors. Nothing may affect real third parties. An agent's network is closed **(automated: `hb` blocks everything else)**: it reaches its model, declared as `OPENAI_BASE_URL` in `runtime.env` **(automated)**, its own services, and nothing more unless its manifest lists a host under `runtime.egress`. Every listed host is a way out, so a maintainer approves each one, and none may be a place where anyone can post content.
2. **Fictional data.** No real secrets, personal data, companies or people. Fake credentials are obviously fake (`sk-arena-FAKE-...`, `ARENA-FAKE-...`). Scenarios use fictional brands, such as ACME Outdoors.
3. **The agent is vulnerable, not the host.** No malware, no real exploits against third-party software, no container-escape attempts, no persistence, no crypto-mining. The agent runs under the arena runtime rules: `cap-drop ALL`, `hb`'s compose allowlist, loopback-only publishing **(automated: compose allowlist)**. Every image names the version it is built on and ends as an unprivileged user **(automated)**, and `hb arena check` holds every container to the container baseline, in CI and on the user's machine: not root, no privileges, no host paths, loopback only **(automated: [smoke test](agent-anatomy.md#testssmokeyaml))**.
4. **Payloads teach, never harm.** Jailbreak and injection demos prove a restriction was bypassed using harmless target content (a pricing secret, a fake discount code). They never contain real harmful instructions.
5. **Declared ground truth.** Every planted vulnerability is declared in `ground_truth`, with every field **(automated: present and complete)**, is mapped to published standards, OWASP and MITRE ATLAS **(automated)**, and has its section in the lab **(automated)**. `ground_truth` is what the agent studies, not everything a test run may find: other findings are outside what the agent covers.
6. **Model-agnostic.** The agent honours `OPENAI_BASE_URL`, an OpenAI-compatible endpoint. This enables local models (Ollama, no paid key) and CI's mock LLM. Other providers are optional extras, and an agent that uses one lists its host under `runtime.egress`. The agent needs no other outbound access, at start-up or later: no package installs, telemetry, fonts or CDN files.
7. **Licensing and size.** When an agent declares `source.upstream`, its license is on the OSI allowlist in `policy_check.py` **(automated)**, and the upstream is credited in the README. Files are at most 1 MB, and there are no binaries other than web images **(automated)**: link media from the README, or attach it to a GitHub Release.

## 6.3 Educational bar

- The agent teaches a distinct vulnerability class, or a clearly new variant of one.
- It has a `difficulty` of easy, medium or hard **(automated)**.
- It has a lab of an hour or less. An agent with more to teach may add a course and a FAQ.
- It does not duplicate an existing agent. A near-duplicate becomes an extra scenario of the existing agent.
- It maps to a vulnerability category and to published standards **(automated)**.

## 6.4 Ground truth

See [ground-truth.md](ground-truth.md). `hb` itself only requires `category` and `description`; `title`, `severity`, `vector` and `success` are required of every agent by the repository's own rules **(automated)**. `source.upstream` is optional and, when present, is validated: a full 40-character commit SHA that the Dockerfile checks out, and a license on the OSI allowlist.

## 6.5 Review flow

Agents are added by Humanbound maintainers. We do not accept pull requests from outside the maintainers for now; anyone may [suggest an agent](https://github.com/humanbound/humanbound-arena/issues/new?template=suggest-an-agent.yml).

1. **Agreement.** Two maintainers agree what is vulnerable, what it teaches and how the damage is contained.
2. **Pull request** from the template, by a maintainer.
3. **Automated checks** (`validate.yml`):
   - DCO sign-off on every commit;
   - `scripts/policy_check.py`: the required skeleton, `id` equals the folder, file sizes and no binaries, the OSI license allowlist (when `source.upstream` is set), a 40-character upstream `ref` that the Dockerfile checks out, image names, `developer {name, url}`, complete `ground_truth`, no `HB_`/`HUMANBOUND_` env names, `OPENAI_BASE_URL` declared, a well-formed `tests/smoke.yaml`, and `index.json` not modified;
   - `hb arena validate` on changed agents, including the compose allowlist;
   - an image build;
   - the smoke test against the mock LLM.
4. **Human review.** A Humanbound maintainer approves containment (hard rules 1–4) and the declared egress. For small agents the same person can do the educational review.
5. **Merge.** `publish.yml` builds and publishes the images (linux/amd64 and linux/arm64), then regenerates `index.json` (recording each pushed image's digest) and the README catalog.
6. **Catalog update.** `main` takes changes through pull requests only, so CI does not push the catalog: it opens a pull request that changes `index.json` and `README.md` and nothing else, and a maintainer merges it. Until then the catalog still lists the agent's previous version.

## 6.6 Lifecycle

- **Versioning.** Each agent has its own semver. Changing planted vulnerabilities or `ground_truth` is a minor bump, or a major bump if existing labs break. Image tags are immutable: publishing an existing version fails, so every change needs a version bump.
- **Upstream updates.** Bumping `source.upstream.ref` is a normal pull request through the same checks.
- **Deprecation.** An agent is marked `deprecated: true` in `index.json` if its nightly smoke has failed for 30 consecutive days (automatic, proposed as a catalog update), or if its maintainer has been unresponsive for 30 days on an agent-bug issue (a maintainer adds a `DEPRECATED` file with the reason). It is removed after 90 more days.
- **Takedown.** An agent that reaches outside its box, or breaks a hard rule, is removed immediately: deleted from `index.json`, and its images deleted. See [SECURITY.md](../SECURITY.md).
