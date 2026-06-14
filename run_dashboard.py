#!/usr/bin/env python
"""Launcher for the World Cup edge dashboard.

Adds ``src`` to the path so it runs without setting PYTHONPATH, then starts the
textual app. If no pipeline state exists yet, press ``r`` inside the app to build it
(or run ``python -m pipeline.orchestrator`` first).

    python run_dashboard.py
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dashboard.app import main  # noqa: E402

if __name__ == "__main__":
    main()
