#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 AI and Me Single-Member Private Company (Humanbound)
#
# Start PriceWatch on this machine for a demo: the competitor's storefront, the agent, and the
# walkthrough page. Ctrl+C stops all three.
#
#   export OPENAI_API_KEY=sk-...
#   agents/pricewatch/lab/start.sh            # then open http://127.0.0.1:8088
#
# The firewall is switched on and off on the page, run by run. The first start makes lab/.venv
# (Python 3.12) and installs the agent's packages into it, and a start after the requirements
# change installs them again (so a raised pin, such as the firewall's, is picked up).
#
# Optional: OPENAI_MODEL, OPENAI_BASE_URL, FIREWALL_JUDGE_MODEL, and the ports
# STOREFRONT_PORT (8001), AGENT_PORT (8000), UI_PORT (8088).
set -euo pipefail

LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT="$(dirname "$LAB")"
VENV="$LAB/.venv"
STOREFRONT_PORT="${STOREFRONT_PORT:-8001}"
AGENT_PORT="${AGENT_PORT:-8000}"
UI_PORT="${UI_PORT:-8088}"

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "Set OPENAI_API_KEY first: export OPENAI_API_KEY=sk-..." >&2
  exit 1
fi

for port in "$STOREFRONT_PORT" "$AGENT_PORT" "$UI_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is in use. Stop what is on it (lsof -nP -iTCP:$port -sTCP:LISTEN), or choose" >&2
    echo "another: STOREFRONT_PORT, AGENT_PORT and UI_PORT set the three ports." >&2
    exit 1
  fi
done

if [ ! -x "$VENV/bin/python" ]; then
  PY="$(command -v python3.12 || true)"
  if [ -z "$PY" ]; then
    echo "Python 3.12 is needed to make lab/.venv (python3.12 not found)." >&2
    exit 1
  fi
  echo "Making lab/.venv (first start only)..."
  "$PY" -m venv "$VENV"
fi
REQUIREMENTS=("$AGENT/services/agent/requirements.txt" "$AGENT/services/storefront/requirements.txt")
INSTALLED="$VENV/.installed-requirements"  # what the last install was given
if ! cat "${REQUIREMENTS[@]}" | cmp -s - "$INSTALLED"; then
  echo "Installing the agent's packages into lab/.venv..."
  "$VENV/bin/pip" install --quiet -r "${REQUIREMENTS[0]}" -r "${REQUIREMENTS[1]}"
  cat "${REQUIREMENTS[@]}" >"$INSTALLED"
fi

TMP="${TMPDIR:-/tmp}"
LOGS="${TMP%/}/pricewatch-lab"
mkdir -p "$LOGS"
PIDS=()
stop() {
  trap - INT TERM EXIT
  echo
  echo "Stopping..."
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap stop INT TERM EXIT

export STOREFRONT_BASE="http://127.0.0.1:$STOREFRONT_PORT"

(cd "$AGENT/services/storefront" &&
  exec "$VENV/bin/python" -m uvicorn app:app --host 127.0.0.1 --port "$STOREFRONT_PORT") \
  >"$LOGS/storefront.log" 2>&1 &
PIDS+=($!)
(cd "$AGENT/services/agent" &&
  exec "$VENV/bin/python" -m uvicorn app:app --host 127.0.0.1 --port "$AGENT_PORT") \
  >"$LOGS/agent.log" 2>&1 &
PIDS+=($!)
"$VENV/bin/python" "$LAB/ui/viewer.py" "http://127.0.0.1:$AGENT_PORT" --port "$UI_PORT" \
  >"$LOGS/viewer.log" 2>&1 &
PIDS+=($!)

wait_for() {  # name, url, log
  for _ in $(seq 1 60); do
    if curl -sf -o /dev/null "$2"; then return 0; fi
    sleep 0.5
  done
  echo "The $1 did not start. Its log, $3:" >&2
  tail -20 "$3" >&2
  exit 1
}
wait_for storefront "$STOREFRONT_BASE/collect/log" "$LOGS/storefront.log"
wait_for agent "http://127.0.0.1:$AGENT_PORT/health" "$LOGS/agent.log"
wait_for "walkthrough page" "http://127.0.0.1:$UI_PORT/" "$LOGS/viewer.log"

cat <<EOF

PriceWatch is running (model: ${OPENAI_MODEL:-gpt-5}).

  Walkthrough page   http://127.0.0.1:$UI_PORT
  Agent              http://127.0.0.1:$AGENT_PORT   (POST /run)
  What leaked        $STOREFRONT_BASE/collect/log

Switch the firewall on and off on the page. Logs: $LOGS. Ctrl+C stops everything.
EOF
wait
