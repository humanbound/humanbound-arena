# Contributing to humanbound-arena

Thank you for wanting to help. Please read this first, because this repository works differently from most.

## We do not accept pull requests for now

Humanbound builds, reviews and publishes every agent here. The agents are vulnerable on purpose, and people run them on their own machines, so what enters the catalog is a security decision that we make ourselves. **Pull requests are open to Humanbound maintainers only.**

This may change. If it does, this page will say so.

## How you can help

| You want to | Do this |
|---|---|
| Report a bug in an agent, or a mistake in a lab | [Open an issue](https://github.com/humanbound/humanbound-arena/issues/new?template=agent-bug.yml) |
| Suggest an agent, or a vulnerability worth teaching | [Suggest an agent](https://github.com/humanbound/humanbound-arena/issues/new?template=suggest-an-agent.yml) |
| Ask a question, or share what you found | [Discussions](https://github.com/humanbound/humanbound-arena/discussions) |
| Report a real security problem | Privately, as [SECURITY.md](SECURITY.md) describes. Never in a public issue. |

A vulnerability that an agent declares is not a security problem: it is the point of the agent.

## For maintainers

How an agent is built is in [docs/adding-an-agent.md](docs/adding-an-agent.md); the rules it must meet are in [docs/admission-policy.md](docs/admission-policy.md).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pip install humanbound                 # the hb CLI, including hb arena
pytest
python scripts/policy_check.py
hb arena validate agents/<id>/arena.yaml
python scripts/smoke.py <id>          # needs Docker
```

Never edit `index.json` by hand. CI generates it and proposes it as a pull request of its own, a catalog update, which a maintainer merges. `main` takes changes through pull requests only, from CI as from people.

Every commit is signed off (`git commit -s`), certifying the [DCO](DCO.md). A CI check enforces it. To fix a branch: `git rebase --signoff origin/main`, then force-push.

## Conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md), in issues and discussions as everywhere else.
