# Security policy

## Use at your own risk

Every agent in this repository is **deliberately vulnerable**: it is built to be tricked into doing what it should not.

We isolate every agent as well as we can. `hb arena` runs each one in a container that cannot read your files, has no privileges, and listens only on your own machine, and CI checks this for every agent. But no isolation is complete, and we do not guarantee this one.

**An agent's network is closed, with one door.** A running agent cannot reach the internet or your machine directly. It can reach its model, its own services, and any host its manifest lists under `runtime.egress`; `hb arena info <id>` shows the list. That narrows what a tricked agent can do. It does not make one safe:

- An allowed destination is still a way out. The door checks where a request goes, not what it carries.
- A container is not a virtual machine.
- On some Docker setups an agent may still reach what the Docker host serves on every network interface; `hb arena run` warns when that is so.
- The network blocking has been verified on Docker Desktop for macOS. On Linux it works by design and has not yet been tested.

So:

- **Use the agents with caution, and at your own risk.**
- **Run them only where there is no sensitive data and no access to critical systems.** Not on a production server, not on a machine inside a network that reaches internal systems, and not on a cloud machine whose credentials matter.
- Give an agent an LLM key with a spending limit, or use a local model.
- Stop the agents when you have finished: `hb arena stop --all`.

The software is provided "as is", without warranty of any kind: see the [license](LICENSE).

## What counts as a security issue here

Because the agents are vulnerable on purpose, a vulnerability in one is not by itself a security issue.

## Not security issues: open a normal issue

- Anything declared in an agent's `ground_truth` (its `arena.yaml`). Those vulnerabilities are the point of the agent.
- New jailbreaks or prompt injections against an agent. `ground_truth` lists what an agent studies, not everything a test run may find, so other findings are expected. They are welcome as ordinary issues, and one worth teaching may be added to the agent.

## Report privately

Report these privately, never in a public issue:

- a container escape, or any impact on the host running an agent;
- a flaw in the arena gateway or the `hb` CLI, such as a loopback bypass or key leakage;
- an agent causing real-world side effects outside its own compose project;
- an agent reaching a destination that is not its model, its own services, or a host its manifest lists under `runtime.egress`;
- a compromised upstream commit, image or dependency.

Channels:

- **Email:** [security@humanbound.ai](mailto:security@humanbound.ai)
- **GitHub Security Advisories:** the "Report a vulnerability" button on this repository's *Security* tab

A clear report includes the agent id and version, what happened, how to reproduce it, and its impact.

## What happens next

We follow the same coordinated-disclosure policy as the [`humanbound` CLI](https://github.com/humanbound/humanbound/blob/main/SECURITY.md):

| Timeline | What to expect |
|---|---|
| Within 72 hours | Acknowledgement that the report was received |
| Within 7 days | Initial triage and severity assessment |
| As soon as practical | A fix, with credit to the reporter unless anonymity is preferred |
| 90 days (default) | Public disclosure window |

## Takedown

An agent that reaches outside its containers or breaks a hard rule of the [admission policy](docs/admission-policy.md) is removed immediately: it is deleted from `index.json` and its images are deleted from the registry.
