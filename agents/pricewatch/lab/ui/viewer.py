# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Humanbound
"""viewer.py — the PriceWatch walkthrough page, served from your machine.

The page draws one run of the agent step by step. It is teaching material, so the agent does not
carry it: this script serves the page and passes its one call, POST /run/stream, on to the agent.
It also serves, read-only, the two files the page's "How it's added" drawer shows, so the drawer is
always the agent's real code: services/agent/agent_core.py and services/agent/agent.yaml.

    hb arena ps                                         # the agent's NATIVE address
    python lab/ui/viewer.py http://127.0.0.1:<port>     # then open http://127.0.0.1:8088

Standard library only. It listens on 127.0.0.1 and talks to the address you give it, nothing else.
"""

from __future__ import annotations

import argparse
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/humanbound.svg": ("humanbound.svg", "image/svg+xml"),
}
SERVICE = HERE.parent.parent / "services" / "agent"
CODE = {  # the only files of the agent the page may read
    "/code/agent_core.py": SERVICE / "agent_core.py",
    "/code/agent.yaml": SERVICE / "agent.yaml",
}
# No proxy from the environment: the agent is on this machine.
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def handler(agent: str) -> type[BaseHTTPRequestHandler]:
    class Viewer(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in CODE:
                body, kind = CODE[path].read_bytes(), "text/plain; charset=utf-8"
            elif path in PAGES:
                body, kind = (HERE / PAGES[path][0]).read_bytes(), PAGES[path][1]
            else:
                return self.send_error(404)
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/run/stream":
                return self.send_error(404)
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            request = urllib.request.Request(
                f"{agent}/run/stream", data=body, headers={"Content-Type": "application/json"}
            )
            try:
                with DIRECT.open(request, timeout=600) as reply:
                    self.send_response(reply.status)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    for line in reply:  # events end with a blank line, so lines arrive as sent
                        self.wfile.write(line)
                        self.wfile.flush()
            except (urllib.error.URLError, OSError) as e:
                self.send_error(502, f"cannot reach the agent at {agent}: {e}")

        def log_message(self, *args):
            pass

    return Viewer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the PriceWatch walkthrough page.")
    parser.add_argument(
        "agent",
        nargs="?",
        default="http://127.0.0.1:8000",
        help="the agent's address: NATIVE in `hb arena ps` (default: a local development run)",
    )
    parser.add_argument("--port", type=int, default=8088, help="where to serve the page")
    args = parser.parse_args(argv)
    agent = args.agent.rstrip("/")
    if not agent.startswith("http://"):
        parser.error("the agent's address starts with http://")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(agent))
    print(f"PriceWatch walkthrough: http://127.0.0.1:{args.port}  (agent: {agent})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
