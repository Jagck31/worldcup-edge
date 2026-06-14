#!/usr/bin/env python
"""Launcher for the World Cup edge pipeline — no PYTHONPATH needed.

    python run_pipeline.py                 # refresh model + live Polymarket + tracker (~2 min)
    python run_pipeline.py --refresh       # also re-download results
    python run_pipeline.py --sims 5000     # more Monte Carlo iterations
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pipeline.orchestrator import main  # noqa: E402

if __name__ == "__main__":
    main()
