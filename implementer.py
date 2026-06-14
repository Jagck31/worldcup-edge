#!/usr/bin/env python
"""Implementer agent — closes the improver loop by actually applying proposals, autonomously.

Each cycle it: picks the top open improver proposal, asks the LLM for the COMPLETE updated
version of ONE allowed source file, applies it ONLY IF the full test suite still passes
(otherwise it instantly restores the original), records the outcome to a shared log the
improver reads next cycle (the "back and forth"), and commits accepted changes to git so any
change is one `git revert` away.

Hard guardrails (this writes code on a live box, unattended):
  * may edit ONLY files under: src/model, src/features, src/simulate, src/edge, src/ingest
    — never the engine/orchestrator core, the agents, deploy/, config.yaml, .env, or anything
    outside this repo (it cannot touch Stock Claude).
  * a change is kept ONLY if `python -m unittest` stays green; else the file is restored.
  * a proposal that fails twice is parked (won't loop forever).
  * touch data/processed/IMPLEMENTER_OFF to pause it; set implementer_enabled: false to disable.

    python implementer.py            # loop (interval from config; default 1h)
    python implementer.py --once     # one attempt (smoke test)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from agents import llm  # noqa: E402
from pipeline.run_live import load_config  # noqa: E402

PROPOSALS = ROOT / "data" / "processed" / "improvement_proposals.json"
LOG_JSON = ROOT / "data" / "processed" / "improvements_log.json"
LOG_MD = ROOT / "IMPROVEMENTS.md"
KILL = ROOT / "data" / "processed" / "IMPLEMENTER_OFF"

ALLOWED_DIRS = ("src/model/", "src/features/", "src/simulate/", "src/edge/", "src/ingest/")
MAX_FILE_LINES = 450
MAX_ATTEMPTS = 2

# Map a proposal's area/keywords to the primary file the change most likely belongs in.
AREA_FILES = {
    "calibrat": "src/model/calibrate.py",
    "feature": "src/features/build_features.py",
    "goal": "src/model/goal_model.py",
    "edge": "src/edge/detect.py",
    "sizing": "src/edge/kelly.py",
    "scan": "src/edge/scanner.py",
    "simulat": "src/simulate/monte_carlo.py",
    "data feed": "src/ingest/livescores.py",
    "feed": "src/ingest/livescores.py",
    "rank": "src/ingest/rankings.py",
    "elo": "src/features/elo.py",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _log_state() -> dict:
    return _load(LOG_JSON, {"entries": []})


def _key(title: str) -> str:
    return " ".join(str(title).lower().split())


def _attempts(log: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in log.get("entries", []):
        counts[_key(e.get("title", ""))] = counts.get(_key(e.get("title", "")), 0) + 1
    return counts


def _implemented(log: dict) -> set[str]:
    return {_key(e["title"]) for e in log.get("entries", []) if e.get("status") == "implemented"}


def pick_proposal(log: dict) -> dict | None:
    data = _load(PROPOSALS, {})
    entries = data.get("entries", [])
    if not entries:
        return None
    latest = entries[-1].get("proposals", [])
    done = _implemented(log)
    attempts = _attempts(log)
    rank = {"high": 0, "med": 1, "medium": 1, "low": 2}
    candidates = [
        p for p in latest
        if _key(p.get("title", "")) not in done and attempts.get(_key(p.get("title", "")), 0) < MAX_ATTEMPTS
    ]
    candidates.sort(key=lambda p: rank.get(str(p.get("impact", "med")).lower(), 1))
    return candidates[0] if candidates else None


def target_file(proposal: dict) -> str | None:
    hay = (str(proposal.get("area", "")) + " " + str(proposal.get("title", "")) + " "
           + str(proposal.get("first_step", ""))).lower()
    for kw, path in AREA_FILES.items():
        if kw in hay:
            rel = path
            full = ROOT / rel
            if full.exists() and any(rel.startswith(d) for d in ALLOWED_DIRS):
                if len(full.read_text(encoding="utf-8").splitlines()) <= MAX_FILE_LINES:
                    return rel
    return None


def run_tests() -> tuple[bool, str]:
    try:
        p = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
            cwd=str(ROOT), env={**__import__("os").environ, "PYTHONPATH": str(SRC)},
            capture_output=True, text=True, timeout=600,
        )
        return p.returncode == 0, (p.stdout + p.stderr)[-1200:]
    except subprocess.SubprocessError as exc:
        return False, f"{type(exc).__name__}: {exc}"


def git(*args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        return 1, "git unavailable"


def ensure_git() -> bool:
    git("config", "--global", "--add", "safe.directory", str(ROOT))
    if (ROOT / ".git").exists():
        return True
    if git("init")[0] != 0:
        return False
    git("config", "user.email", "implementer@worldcup.local")
    git("config", "user.name", "wc-implementer")
    git("add", "-A")
    git("commit", "-m", "baseline before autonomous improvements")
    return True


def record(entry: dict) -> None:
    log = _log_state()
    log.setdefault("entries", []).append(entry)
    log["entries"] = log["entries"][-200:]
    log["updated_at"] = _now()
    try:
        tmp = LOG_JSON.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(log, indent=2), encoding="utf-8")
        tmp.replace(LOG_JSON)
    except OSError:
        pass
    if entry.get("status") == "implemented":
        try:
            with LOG_MD.open("a", encoding="utf-8") as f:
                f.write(f"\n## {entry['at']} — {entry['title']}\n"
                        f"- **file:** `{entry.get('file')}`  ·  **commit:** `{entry.get('commit','-')}`\n"
                        f"- {entry.get('summary','')}\n")
        except OSError:
            pass


def attempt(proposal: dict, model: str | None, restart: bool) -> dict:
    title = proposal.get("title", "?")
    rel = target_file(proposal)
    base = {"at": _now(), "title": title, "area": proposal.get("area"), "impact": proposal.get("impact")}
    if not rel:
        return {**base, "status": "skipped", "reason": "no allowed/sized target file for this proposal"}
    path = ROOT / rel
    original = path.read_text(encoding="utf-8")
    prompt = (
        "You are improving a World Cup betting model. Implement the proposal below by editing "
        f"EXACTLY ONE file: {rel}. Return the COMPLETE updated file — keep all existing public "
        "functions/behaviour, make a focused, correct, minimal change, keep imports valid. "
        'Return STRICT JSON: {"summary":"one line of what you changed","new_content":"<entire file>"}.\n\n'
        f"PROPOSAL: {json.dumps(proposal)}\n\nCURRENT {rel}:\n```python\n{original}\n```"
    )
    out = llm.chat(
        [{"role": "system", "content": "You are a careful Python engineer. Return only valid JSON with the full file."},
         {"role": "user", "content": prompt}],
        model=model, max_tokens=4000, temperature=0.2,
    )
    if not out:
        return {**base, "status": "failed", "file": rel, "reason": "no LLM output"}
    try:
        if out.startswith("```"):
            out = out[out.find("{"):out.rfind("}") + 1]
        payload = json.loads(out)
        new_content = payload.get("new_content", "")
        summary = payload.get("summary", "")
    except (json.JSONDecodeError, ValueError):
        return {**base, "status": "failed", "file": rel, "reason": "unparseable LLM JSON"}
    # sanity: plausible size, looks like the same module
    if not new_content.strip() or not (0.5 * len(original) <= len(new_content) <= 4 * len(original)):
        return {**base, "status": "failed", "file": rel, "reason": "implausible file size returned"}

    path.write_text(new_content, encoding="utf-8")
    ok, tail = run_tests()
    if not ok:
        path.write_text(original, encoding="utf-8")  # instant restore
        return {**base, "status": "reverted", "file": rel, "summary": summary,
                "reason": "tests failed", "test_tail": tail[-400:]}
    commit = "-"
    if ensure_git():
        git("add", "-A")
        if git("commit", "-m", f"auto-improve: {title}")[0] == 0:
            commit = git("rev-parse", "--short", "HEAD")[1]
    if restart:
        subprocess.run(["systemctl", "restart", "wc-engine"], capture_output=True)
    return {**base, "status": "implemented", "file": rel, "summary": summary, "commit": commit}


def run_once(model: str | None, restart: bool) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    if KILL.exists():
        print(f"[{stamp}] implementer paused (IMPLEMENTER_OFF present).", flush=True)
        return
    if not llm.have_key():
        print(f"[{stamp}] implementer: no OPENAI_API_KEY — idle.", flush=True)
        return
    proposal = pick_proposal(_log_state())
    if not proposal:
        print(f"[{stamp}] implementer: no open proposals to work on.", flush=True)
        return
    print(f"[{stamp}] implementer: attempting '{proposal.get('title')}'…", flush=True)
    entry = attempt(proposal, model, restart)
    record(entry)
    print(f"[{stamp}] -> {entry['status'].upper()}: {entry.get('summary') or entry.get('reason','')}"
          + (f" ({entry.get('file')} @ {entry.get('commit')})" if entry['status'] == 'implemented' else ""), flush=True)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="World Cup implementer agent.")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=None)
    ap.add_argument("--no-restart", action="store_true", help="don't restart wc-engine after a change")
    args = ap.parse_args()

    config = load_config(ROOT / "config.yaml")
    llm.load_env(ROOT / ".env")
    if not config.get("implementer_enabled", True):
        print("implementer disabled (implementer_enabled: false) — Claude is the implementer. Idling.", flush=True)
        if args.once:
            return
        while True:  # stay running (idle) so systemd doesn't restart-loop us
            try:
                time.sleep(3600)
            except KeyboardInterrupt:
                return
    interval = args.interval or int(config.get("implementer_interval_sec", 3600))
    model = config.get("implementer_model") or config.get("improver_model") or None
    restart = not args.no_restart

    print(f"implementer: every {interval}s, llm={'on' if llm.have_key() else 'OFF'}, "
          f"test-gated + git-revertible, edits only {ALLOWED_DIRS}. Ctrl+C to stop.", flush=True)
    while True:
        try:
            run_once(model, restart)
        except KeyboardInterrupt:
            break
        except Exception as exc:  # noqa: BLE001
            print(f"implementer cycle error: {type(exc).__name__}: {exc}", flush=True)
        if args.once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
