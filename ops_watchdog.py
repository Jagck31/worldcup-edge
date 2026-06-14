#!/usr/bin/env python
"""Ops watchdog — keeps the World Cup site running and explains problems in plain English.

Every cycle it checks the web app, the live engine, and the systemd services, then:
  * auto-heals BOUNDED problems by restarting ONLY the wc-* services (with a cooldown),
  * if anything's wrong and an OpenAI key is present, asks the model for a short diagnosis
    + suggested fix from the signals and recent logs,
  * writes data/processed/ops_report.json (the dashboard shows it) and logs to journald.

It never touches Stock Claude or anything outside the wc-* services, and never edits code.

    python ops_watchdog.py                 # loop forever (interval from config)
    python ops_watchdog.py --once          # single check (smoke test)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from agents import llm  # noqa: E402
from pipeline.run_live import load_config  # noqa: E402

STATE = ROOT / "data" / "processed" / "dashboard_state.json"
REPORT = ROOT / "data" / "processed" / "ops_report.json"
WC_SERVICES = ("wc-engine", "wc-web")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _http_ok(url: str, timeout: float = 6.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return (200 <= r.status < 400), str(r.status)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}"


def _systemctl(*args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=20)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def _is_active(unit: str) -> bool:
    code, out = _systemctl("is-active", unit)
    return out.strip() == "active"


def _journal_tail(unit: str, n: int = 40) -> str:
    try:
        p = subprocess.run(["journalctl", "-u", unit, "-n", str(n), "--no-pager", "-o", "cat"],
                           capture_output=True, text=True, timeout=20)
        return p.stdout.strip()[-2500:]
    except (FileNotFoundError, subprocess.SubprocessError):
        return ""


def collect(config: dict, port: int) -> dict:
    """Gather health signals — no LLM needed; this is the source of truth for actions."""
    issues: list[str] = []
    web_ok, web_code = _http_ok(f"http://127.0.0.1:{port}/healthz")
    if not web_ok:
        issues.append(f"web app not responding on :{port} ({web_code})")

    state = {}
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        issues.append("dashboard_state.json missing/unreadable")

    eng = state.get("engine", {})
    jobs = eng.get("jobs", {})
    # staleness: the engine should write at least every ~2x its slowest live loop
    gen = state.get("generated_at")
    stale_sec = None
    if gen:
        try:
            t = datetime.strptime(gen, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            stale_sec = (datetime.now(timezone.utc) - t).total_seconds()
        except ValueError:
            pass
    max_interval = max([int(j.get("interval_sec", 90)) for j in jobs.values()] or [90])
    if stale_sec is not None and stale_sec > max(600, 3 * max_interval):
        issues.append(f"engine state stale ({int(stale_sec)}s old)")
    for name, j in jobs.items():
        if j.get("status") == "error":
            issues.append(f"job '{name}' errored: {j.get('detail', '')[:80]}")
    for unit in WC_SERVICES:
        if not _is_active(unit):
            issues.append(f"service {unit} not active")

    eq = state.get("paper_account", {}).get("summary", {}).get("equity")
    return {
        "web_ok": web_ok,
        "engine_live": bool(eng.get("live")),
        "stale_sec": stale_sec,
        "equity": eq,
        "n_jobs": len(jobs),
        "job_errors": [n for n, j in jobs.items() if j.get("status") == "error"],
        "recent_errors": eng.get("recent_errors", [])[-3:],
        "issues": issues,
    }


def auto_heal(signals: dict, last_restart: dict, cooldown: int) -> list[str]:
    """Restart ONLY wc-* services for clear failures, rate-limited per service."""
    actions: list[str] = []
    now = time.monotonic()

    def restart(unit: str, why: str) -> None:
        if now - last_restart.get(unit, -1e9) < cooldown:
            actions.append(f"skipped restart {unit} (cooldown) — {why}")
            return
        code, out = _systemctl("restart", unit)
        last_restart[unit] = now
        actions.append(f"restarted {unit} ({'ok' if code == 0 else 'FAILED: ' + out[:80]}) — {why}")

    if not signals["web_ok"] or not _is_active("wc-web"):
        restart("wc-web", "web unresponsive")
    stale = signals.get("stale_sec")
    if not _is_active("wc-engine"):
        restart("wc-engine", "engine service down")
    elif stale is not None and stale > 1800:
        restart("wc-engine", f"engine stale {int(stale)}s")
    return actions


def diagnose(signals: dict, actions: list[str], model: str | None) -> str | None:
    """Optional LLM summary of what's wrong and what to check next."""
    if not signals["issues"] or not llm.have_key():
        return None
    logs = _journal_tail("wc-engine", 30) if signals.get("job_errors") or signals.get("stale_sec") else ""
    prompt = (
        "You are the on-call SRE for a World Cup betting dashboard (a live engine + a web app, "
        "two systemd services behind Tailscale). Given these health signals, write a 2-3 sentence "
        "diagnosis and the single most likely fix. Be concrete and terse; no preamble.\n\n"
        f"SIGNALS:\n{json.dumps(signals, indent=2)}\n\n"
        f"AUTO-ACTIONS TAKEN THIS CYCLE:\n{actions}\n\n"
        f"RECENT wc-engine LOG:\n{logs or '(not collected)'}"
    )
    return llm.chat(
        [{"role": "system", "content": "You are a concise, accurate site-reliability engineer."},
         {"role": "user", "content": prompt}],
        model=model, max_tokens=220,
    )


def run_once(config: dict, port: int, last_restart: dict, cooldown: int, model: str | None) -> dict:
    signals = collect(config, port)
    healthy = not signals["issues"]
    actions = [] if healthy else auto_heal(signals, last_restart, cooldown)
    summary = None if healthy else diagnose(signals, actions, model)
    report = {
        "checked_at": _now(),
        "status": "OK" if healthy else ("DOWN" if not signals["web_ok"] else "DEGRADED"),
        "issues": signals["issues"],
        "actions": actions,
        "llm_summary": summary,
        "signals": signals,
        "llm_enabled": llm.have_key(),
    }
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        tmp = REPORT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
        tmp.replace(REPORT)
    except OSError:
        pass
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    line = f"[{stamp}] {report['status']}"
    if signals["issues"]:
        line += " :: " + "; ".join(signals["issues"])
    if actions:
        line += " :: " + "; ".join(actions)
    if summary:
        line += f"\n   diagnosis: {summary}"
    print(line, flush=True)
    return report


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="World Cup ops watchdog.")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=None)
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()

    config = load_config(ROOT / "config.yaml")
    llm.load_env(ROOT / ".env")
    interval = args.interval or int(config.get("ops_interval_sec", 120))
    port = args.port or int(config.get("web_port", 8000))
    cooldown = int(config.get("ops_restart_cooldown_sec", 600))
    model = config.get("ops_model") or None
    last_restart: dict = {}

    print(f"ops watchdog: every {interval}s, port {port}, llm={'on' if llm.have_key() else 'off'}, "
          f"auto-heal={WC_SERVICES} (cooldown {cooldown}s). Ctrl+C to stop.", flush=True)
    while True:
        try:
            run_once(config, port, last_restart, cooldown, model)
        except KeyboardInterrupt:
            break
        except Exception as exc:  # noqa: BLE001
            print(f"watchdog cycle error: {type(exc).__name__}: {exc}", flush=True)
        if args.once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
