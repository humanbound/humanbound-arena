<!-- Pull requests are open to Humanbound maintainers only. See CONTRIBUTING.md. -->

## What this changes

<!-- The agent(s) touched and why. -->

## Checklist

- [ ] Every commit is signed off (`git commit -s`, see [DCO.md](../DCO.md))
- [ ] For a new agent, a second maintainer has agreed what it teaches and how the damage is contained
- [ ] The [hard rules](../docs/admission-policy.md#62-hard-rules) hold: contained damage, fictional data, the agent is vulnerable (not the host), payloads teach and never harm, complete ground truth, `OPENAI_BASE_URL` honoured, licences and sizes OK
- [ ] Every planted vulnerability is declared in `ground_truth` ([guide](../docs/ground-truth.md))
- [ ] `lab/README.md` walks through each declared vulnerability, in an hour or less
- [ ] The agent folder holds only the deployment and `lab/`
- [ ] `README.md` and `lab/README.md` open with the safety notice, unchanged
- [ ] `python scripts/policy_check.py <id>` and `hb arena validate <path>/arena.yaml` pass
- [ ] `python scripts/smoke.py <id>` passes locally
- [ ] The agent's `version` is bumped (image tags are immutable)
- [ ] `index.json` is not edited (CI regenerates it, and proposes it as a pull request of its own)
