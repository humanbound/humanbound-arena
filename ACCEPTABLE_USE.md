# Acceptable use

> ⚠️ **Deliberately vulnerable. Use at your own risk.** We isolate every agent as well as we can, but no isolation is complete. Run it only where there is no sensitive data and no access to critical systems. See [SECURITY.md](SECURITY.md).

The agents in this repository are deliberately vulnerable. They exist for **local testing, education and research**: to learn how AI agents fail, and to practise testing them with the [`hb` CLI](https://github.com/humanbound/humanbound).

`hb arena` runs every agent in Docker, on a network with no way out, reachable only from your own machine. Keep it that way.

## You must not

- **Deploy an agent on the public internet**, or on any network where people other than you can reach it.
- **Run an agent where there is sensitive data or access to critical systems**: a production server, a machine inside a network that reaches internal systems, a cloud machine whose credentials matter.
- **Use an agent as a lure or honeypot** against third parties.
- **Point an agent's attack payloads at systems you don't own** or aren't authorised to test. The payloads in labs and benchmarks target the agent's own fictional services (its storefronts, mailboxes and collectors), and they should stay there.

## Also keep in mind

- The data in every agent is fictional. Don't add real secrets, personal data, companies or people when you extend one.
- An agent can reach its model, and an allowed destination is still a way out. Use an LLM key with a spending limit, or a local model through `OPENAI_BASE_URL`.

If you find an agent doing something outside its box, see [SECURITY.md](SECURITY.md).
