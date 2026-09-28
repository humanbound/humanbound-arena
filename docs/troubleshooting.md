# Troubleshooting

Setup problems common to every arena agent. Questions about one agent's topic are in its own lab (`lab/README.md`, and whatever else its `lab/` folder holds).

**`hb arena run <id>` says Docker is not running.**
Start Docker Desktop (or the Docker daemon on Linux) and run it again. `docker info` should succeed first.

**It says `<id> needs OPENAI_API_KEY`.**
Set the key for the arena: `hb arena config set OPENAI_API_KEY=sk-...`. For a local model that doesn't check keys, any value works (`OPENAI_API_KEY=ollama`). `hb` reads keys from your shell only for agents from the default Humanbound catalog; for any other catalog (a local checkout, or a fork) it never reads the shell, so `hb arena config set` (or `--env-file`) is the only way.

**Every reply is an error about reaching the LLM.**
Check `hb arena logs <id>`. With a local model, the agent runs inside Docker, so `localhost` means the container, not your machine: use `http://host.docker.internal:<port>/v1` with Docker Desktop, or your machine's IP address on Linux, and make sure the model server listens on all interfaces (for Ollama, `OLLAMA_HOST=0.0.0.0`).

**The agent answers with `HTTP 401` or `HTTP 503` from the LLM.**
401: the key is wrong or revoked; set it again with `hb arena config set`. 503 ("Unable to verify model access right now"): the provider is having a moment; wait and retry. `hb test` reports conversations that fail this way as *errored* and leaves them out of the grade.

**`hb test` says `No LLM provider configured`.**
`hb test` needs its own model to generate attacks and judge replies, separate from the agent's key: `export HB_PROVIDER=openai HB_API_KEY=sk-...` (optionally `HB_MODEL=gpt-4.1-mini`), or `hb config set provider openai` and `hb config set api-key sk-...`.

**A lab command prints a Python traceback instead of a reply.**
The gateway returned an error instead of a message, usually because the agent is not running or its LLM call failed. Run `hb arena ps` and `hb arena logs <id>`.

**An attack from a lab doesn't work on my model.**
Models differ, and the same model varies from run to run: labs quote success rates, not certainties. Retry in a fresh conversation (a new `contextId`), rephrase, or combine tricks. `hb test` tries many strategies for you.

**The agent remembers an earlier attack.**
Conversations are kept per `contextId`. Start a new one, or wipe everything with `hb arena reset <id>`.

**How much does a lab cost?**
With `gpt-4o-mini` (the default for most agents), a few cents per lab. `hb test` makes many more calls: check your provider's usage page after a run. A local model costs nothing.

**The agent cannot reach something it needs.**
Every agent's network is closed: it reaches its model, its own services, and the hosts its manifest lists. `hb arena logs <id> --door` shows each destination it tried, and whether it was allowed or blocked. `hb arena info <id>` shows what it may reach. If you run a model on your own machine, set `OPENAI_BASE_URL` to `http://host.docker.internal:<port>/v1`; only that port is opened.

**My `curl` gets "missing or invalid arena gateway token".**
Every call to the gateway needs its access token. `hb arena token` prints it, and `hb arena endpoint <id>` prints a `curl` command that carries it (`-H 'Authorization: Bearer <token>'`). `hb test --target arena://<id>` sends it for you.

**Port 11500 is already in use.**
Another gateway (or another program) holds it. `hb arena stop --all` stops ours; to use another port, `export HB_ARENA_PORT=11600` wherever you run `hb arena` and `hb test`.
