# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 AI and Me Single-Member Private Company (Humanbound)
"""Make the agent's modules (services/agent) importable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "agent"))
