"""Always-on live engine: the single writer that keeps every dashboard section live.

The full pipeline, the live results tracker, and the price watcher used to be three
separate scripts that each read-modify-wrote ``dashboard_state.json`` -- run together they
race and clobber each other's sections. This module folds them into ONE process with one
writer and several staggered jobs, so the model, probabilities, edges, portfolio, paper
account, live tracker and Elo are all refreshed continuously and coherently:

  * prices    (~60s)  -> live Polymarket CLOB -> edges -> portfolio -> paper account
  * results   (~90s)  -> live results feed    -> re-score frozen picks -> Elo -> tracker
  * simulate  (on every new result, else ~15m) -> Monte Carlo on current Elo -> champion/
                        advance/finalist probabilities -> cached outcomes for the portfolio
  * retrain   (~6h)   -> full pipeline (re-trains the model + refreshes data/backtest)

The trained model + goal model are built once (``build_context``) and reused, so the tight
loops are cheap. Every persist is atomic (temp file + ``os.replace``) so the web reader
never sees a half-written file. The freeze-at-kickoff invariant is preserved (a finished
game is always scored against its pre-match prediction, never re-predicted with leaked Elo).

This is a library; ``live_engine.py`` at the repo root drives it with a rich terminal UI.
"""
from __future__ import annotations

import json
import os
import random
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from edge.portfolio import build_portfolio
from features.build_features import HOST_NATIONS_2026
from features.elo import EloConfig, EloEngine
from ingest.livescores import LiveScoreClient
from ingest.results import TeamNameNormalizer
from pipeline.live_tracker import (
    TrackerContext,
    build_context,
    frozen_from_predictions,
    load_manual_seed_events,
    merge_finished_into_csv,
    recompute,
)
from pipeline.orchestrator import (
    ALIASES_PATH,
    MANUAL_RESULTS_SEED_PATH,
    PAPER_ACCOUNT_PATH,
    SIM_OUTCOMES_PATH,
    STATE_PATH,
    THIRD_PLACE_PATH,
    WC_RESULTS_PATH,
    PoissonScorelineSampler,
    _build_live_markets,
    _build_markets,
    _elo_win_probability,
    _live_wc_rows,
    _slate_to_bets,
    run_pipeline,
)
from pipeline.paper_account import load_account, save_account, update_account
from pipeline.paper_trader import live_execution_status
from pipeline.tracker import build_sim_groups, overlay_results
from simulate.monte_carlo import TournamentSimulator, load_third_place_assignment_table

CONTROL_PATH = STATE_PATH.parent / "engine_control.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm(title) -> str:
    return " ".join(str(title or "").lower().split())


@dataclass
class Job:
    """One periodic unit of work with its own cadence, trigger flag, and last-run telemetry."""

    name: str
    interval: float
    fn: Callable[[], str]
    last_run: float = 0.0          # time.monotonic() of last completion (0 = never)
    next_due: float = 0.0          # time.monotonic() when it should next run
    running: bool = False
    triggered: bool = False        # force-run on the next tick regardless of cadence
    last_status: str = "pending"   # ok | warn | error | running | pending
    last_detail: str = ""
    last_seconds: float = 0.0
    runs: int = 0
    errors: int = 0

    def due(self, now: float) -> bool:
        return self.triggered or now >= self.next_due


class LiveEngine:
    """Owns the in-memory dashboard state and is the sole writer of dashboard_state.json."""

    def __init__(
        self,
        config: dict,
        history_years: int = 12,
        listener: Callable[["LiveEngine", str], None] | None = None,
    ) -> None:
        self.config = config
        self.history_years = history_years
        self.listener = listener or (lambda engine, event: None)

        self.sim_n = int(config.get("engine_sim_n", 50_000))
        self.bankroll = float(config.get("bankroll_usd", 75))
        # Adaptive results cadence: poll faster while any match is in-play.
        self._results_base = float(config.get("engine_results_interval_sec", 90))
        self._results_live = float(config.get("engine_results_live_interval_sec", 30))

        self.state: dict = {}
        self.ctx: TrackerContext | None = None
        self.client: LiveScoreClient | None = None
        self.normalizer = TeamNameNormalizer.from_yaml(ALIASES_PATH)

        # Cross-cycle caches so the tight loops stay cheap.
        self._elo = None                       # latest EloEngine result (built each results cycle)
        self._samples: list[dict] = []         # cached simulated market outcomes for the portfolio
        self._sim_groups: dict | None = None   # {group: {teams, fixtures}} with locked results
        self._frozen: dict = {}                # pre-match predictions, locked at kickoff
        self._prev_completed: set = set()      # which games were already scored (for "just in" alerts)
        self._newly_finished: list[str] = []   # human labels for games that finished this cycle
        self._equity_curve: list[dict] = []    # rolling (t, equity) series for the live sparkline
        self.started_at = time.time()
        self.last_write = 0.0
        self.errors: list[str] = []            # recent error lines (most recent last)

        self.jobs: dict[str, Job] = {
            "results": Job("results", float(config.get("engine_results_interval_sec", 90)), self.job_results),
            "prices": Job("prices", float(config.get("engine_price_interval_sec", 60)), self.job_prices),
            "simulate": Job("simulate", float(config.get("engine_resim_interval_sec", 900)), self.job_simulate),
            "retrain": Job("retrain", float(config.get("engine_retrain_interval_sec", 21_600)), self.job_retrain),
        }

    # ----- lifecycle -------------------------------------------------------------------

    def _emit(self, event: str) -> None:
        try:
            self.listener(self, event)
        except Exception:  # a broken UI must never take down the engine
            pass

    def _log_error(self, where: str, exc: BaseException) -> None:
        line = f"{datetime.now().strftime('%H:%M:%S')}  {where}: {type(exc).__name__}: {exc}"
        self.errors.append(line)
        self.errors = self.errors[-12:]

    def _acquire_lock(self) -> None:
        """Best-effort single-instance guard so two engines never write the ledger at once.
        POSIX flock (the deploy target); silently skipped on Windows dev."""
        try:
            import fcntl
        except ImportError:
            return
        lock_path = STATE_PATH.parent / "engine.lock"
        try:
            self._lock_fh = open(lock_path, "w")
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fh.write(str(os.getpid()))
            self._lock_fh.flush()
        except (OSError, BlockingIOError) as exc:
            raise SystemExit(f"Another live engine already holds {lock_path} — refusing to start a second writer ({exc}).")

    def startup(self) -> None:
        """Train the model once, seed state, and run one pass of every live job."""
        self._acquire_lock()
        self._emit("startup:context")
        self.ctx = build_context(self.config, history_years=self.history_years)
        self.client = LiveScoreClient.from_config(self.config, self.ctx.normalizer)
        self.normalizer = self.ctx.normalizer

        # Seed state: reuse a recent on-disk snapshot for the slow-moving sections (model,
        # data, squads, backtest); if none exists or it's unusable, build a full one now.
        self.state = self._load_or_build_state()

        # Seed the freeze baseline + already-scored set from the loaded predictions so a
        # restart doesn't re-announce old results as new.
        prior = self.state.get("tracker", {}).get("predictions", [])
        self._frozen = frozen_from_predictions(prior)
        self._prev_completed = {
            frozenset((p["home"], p["away"])) for p in prior if p.get("status") == "completed"
        }
        if SIM_OUTCOMES_PATH.exists():
            try:
                self._samples = json.loads(SIM_OUTCOMES_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._samples = []
        self._equity_curve = self.state.get("equity_curve", []) or []

        # One immediate pass of each live job so the dashboard is fresh within seconds:
        # results first (gives us the current Elo), then simulate, then prices.
        self._emit("startup:results")
        self._run_job(self.jobs["results"], force=True)
        self._emit("startup:simulate")
        self._run_job(self.jobs["simulate"], force=True)
        self._emit("startup:prices")
        self._run_job(self.jobs["prices"], force=True)
        self.jobs["retrain"].last_run = time.monotonic()
        self.jobs["retrain"].next_due = time.monotonic() + self.jobs["retrain"].interval
        self._persist()
        self._emit("startup:done")

    def _load_or_build_state(self) -> dict:
        if STATE_PATH.exists():
            try:
                state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                if state.get("model") and state.get("simulation"):
                    state["config"] = self.config
                    return state
            except (json.JSONDecodeError, OSError):
                pass
        self._emit("startup:fullrun")
        state = run_pipeline(refresh=False, n_sims=self.sim_n, history_years=self.history_years)
        return state

    def run(self, once: bool = False, tick: float = 1.0) -> None:
        """Main loop: each tick, run any due/triggered job, then sleep briefly."""
        self.startup()
        if once:
            return
        while True:
            try:
                self._check_control()
                now = time.monotonic()
                for job in self._due_jobs(now):
                    self._run_job(job)
                    self._persist()
                self._emit("tick")
                time.sleep(tick)
            except KeyboardInterrupt:
                break
            except Exception as exc:  # the loop itself must never die
                self._log_error("loop", exc)
                time.sleep(tick)

    def _due_jobs(self, now: float) -> list[Job]:
        # Stable order so a slow job can't starve another; retrain last (it's heavy).
        order = ["results", "simulate", "prices", "retrain"]
        return [self.jobs[name] for name in order if self.jobs[name].due(now)]

    def _run_job(self, job: Job, force: bool = False) -> None:
        job.running = True
        job.triggered = False
        job.last_status = "running"
        self._emit(f"job:{job.name}:start")
        start = time.perf_counter()
        try:
            job.last_detail = job.fn() or ""
            job.last_status = "ok"
            job.runs += 1
        except Exception as exc:
            job.last_status = "error"
            job.last_detail = f"{type(exc).__name__}: {exc}"
            job.errors += 1
            self._log_error(job.name, exc)
        finally:
            job.last_seconds = round(time.perf_counter() - start, 2)
            job.running = False
            job.last_run = time.monotonic()
            job.next_due = job.last_run + job.interval
            self._emit(f"job:{job.name}:done")

    def trigger(self, name: str) -> None:
        job = self.jobs.get(name)
        if job is not None:
            job.triggered = True

    def _check_control(self) -> None:
        """Honour a control request dropped by the web UI (force a re-sim / refresh)."""
        if not CONTROL_PATH.exists():
            return
        try:
            req = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
            CONTROL_PATH.unlink(missing_ok=True)
        except (json.JSONDecodeError, OSError):
            return
        if req.get("resim") or req.get("refresh"):
            self.trigger("simulate")
            self.trigger("prices")
        if req.get("results"):
            self.trigger("results")
        if req.get("retrain"):
            self.trigger("retrain")
        if req.get("resolve"):
            self._record_resolutions(req["resolve"])
            self._persist()  # reflect the cleared proposal on the dashboard immediately

    # ----- jobs ------------------------------------------------------------------------

    def job_results(self) -> str:
        """Poll the live results feed, merge finished games, re-score frozen picks, refresh Elo."""
        assert self.ctx is not None and self.client is not None
        events = self.client.fetch_events()
        deltas = merge_finished_into_csv(events, WC_RESULTS_PATH, self.normalizer)
        # Gap-fill from the git-tracked manual seed: games the free feed never carried
        # (e.g. Australia-Turkiye, Netherlands-Japan, Sweden-Tunisia). Feed always wins.
        seed_events = load_manual_seed_events(MANUAL_RESULTS_SEED_PATH, self.normalizer)
        if seed_events:
            seed_deltas = merge_finished_into_csv(seed_events, WC_RESULTS_PATH, self.normalizer, fill_only=True)
            if seed_deltas.get("new"):
                deltas["new"] = deltas.get("new", []) + seed_deltas["new"]
        in_play_pairs = {e.pair for e in events if e.in_play}
        result = recompute(self.ctx, self._frozen, in_play_pairs, refresh_forward=True)
        self._elo = result.get("elo")  # reuse for the next simulate without rebuilding

        completed_now = {
            frozenset((p["home"], p["away"]))
            for p in result["predictions"]
            if p.get("status") == "completed"
        }
        newly = completed_now - self._prev_completed
        self._newly_finished = [
            f"{e.home} {e.home_score}-{e.away_score} {e.away}" for e in deltas.get("new", [])
        ]

        # Rich in-play snapshot (score + minute/status) merged with each game's pre-match pick.
        pick_by_pair = {frozenset((p["home"], p["away"])): p for p in result["predictions"]}
        live_games = []
        for e in events:
            if not e.in_play:
                continue
            pk = pick_by_pair.get(e.pair, {})
            pick = pk.get("pick")
            pick_team = e.home if pick == "H" else (e.away if pick == "A" else "Draw")
            live_games.append({
                "home": e.home, "away": e.away,
                "home_score": e.home_score or 0, "away_score": e.away_score or 0,
                "status": e.status_raw or "LIVE",
                "pick": pick_team, "pick_prob": pk.get("pick_prob"),
            })
        live_games.sort(key=lambda g: (g["home"], g["away"]))

        tracker = self.state.setdefault("tracker", {})
        tracker["predictions"] = result["predictions"]
        tracker["scorecard"] = result["scorecard"]
        tracker["n_fixtures"] = len(result["predictions"])
        tracker.setdefault("data_cutoff", "2026-06-12")
        tracker["live"] = {
            "updated_at": _now_iso(),
            "source": f"{self.client.base_url.split('//')[-1].split('/')[0]} ({len(events)} events)",
            "results_fed": result.get("live_results_fed", 0),
            "in_play": [f"{g['home']} {g['home_score']}-{g['away_score']} {g['away']} ({g['status']})" for g in live_games],
            "games": live_games,
            "newly_finished": self._newly_finished,
        }
        # Adaptive cadence: tighten the results loop while anything is live, relax otherwise.
        self.jobs["results"].interval = self._results_live if live_games else self._results_base
        elo_state = self.state.setdefault("elo", {})
        elo_state["leaderboard"] = result["elo_leaderboard"][:60]
        elo_state["live_results_fed"] = result.get("live_results_fed", 0)
        elo_state["n_teams_rated"] = len(result["elo_leaderboard"])

        self._frozen = frozen_from_predictions(result["predictions"])
        self._prev_completed = completed_now

        if deltas.get("new") or deltas.get("changed"):
            self.trigger("simulate")  # a new result moves Elo -> probabilities must re-sim

        sc = result["scorecard"].get("completed", {})
        played = sc.get("n", 0)
        extra = f", +{len(deltas['new'])} new" if deltas.get("new") else ""
        return f"{len(events)} events, {played} scored{extra}"

    def job_simulate(self) -> str:
        """Monte Carlo on the current Elo + goal model so championship probabilities stay live."""
        assert self.ctx is not None
        ctx = self.ctx
        wc_results = pd.read_csv(WC_RESULTS_PATH) if WC_RESULTS_PATH.exists() else None
        fixtures = overlay_results(ctx.base_fixtures, wc_results, self.normalizer)
        sim_groups = build_sim_groups(fixtures)
        self._sim_groups = sim_groups

        elo = self._elo if self._elo is not None else self._build_elo()
        self._elo = elo
        sampler = PoissonScorelineSampler(ctx.goal_model, elo=elo, seed=1)
        third = None
        if THIRD_PLACE_PATH.exists():
            try:
                third = load_third_place_assignment_table(THIRD_PLACE_PATH)
            except Exception:
                third = None
        simulator = TournamentSimulator(
            scoreline_sampler=sampler,
            knockout_win_probability=_elo_win_probability(elo),
            third_place_assignment_table=third,
            rng=random.Random(2026),
        )
        submarkets = simulator.simulate_many(sim_groups, n_sims=self.sim_n)
        self._samples = simulator.sample_market_outcomes(sim_groups, n_sims=min(self.sim_n, 10_000))
        try:
            SIM_OUTCOMES_PATH.write_text(json.dumps(self._samples), encoding="utf-8")
        except OSError:
            pass

        self.state["simulation"] = {
            "n_sims": self.sim_n,
            "draw_label": "OFFICIAL 2026 DRAW",
            "groups": ctx.display_groups,
            "submarkets": [
                {k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()}
                for row in submarkets.to_dict("records")
            ],
            "sample_bracket": simulator.last_bracket,
            "updated_at": _now_iso(),
        }
        champ = submarkets.iloc[0]
        return f"favourite {champ['team']} {champ['p_champion'] * 100:.1f}%"

    def job_prices(self) -> str:
        """Re-fetch live order books, re-detect edges, re-optimise the book, update the account."""
        assert self.ctx is not None
        sim = self.state.get("simulation", {})
        submarkets = pd.DataFrame(sim.get("submarkets", []))
        if submarkets.empty:
            return "no submarkets yet"

        live = None
        if self.config.get("use_live_markets", True):
            try:
                live = _build_live_markets(submarkets, self.normalizer, self.config)
            except Exception as exc:
                self._log_error("prices/live", exc)
                live = None
        if live is not None:
            self.state["markets"] = live
        else:
            groups = self._sim_groups or {}
            self.state["markets"] = _build_markets(submarkets, groups, self.config)

        markets = self.state["markets"]
        slate = markets.get("slate", [])
        team_to_group = {t: g for g, teams in self.ctx.display_groups.items() for t in teams}
        bets = _slate_to_bets(slate, team_to_group, self.bankroll)
        if bets and self._samples:
            try:
                self.state["portfolio"] = build_portfolio(bets, self._samples, bankroll_usd=self.bankroll)
            except Exception as exc:
                self._log_error("prices/portfolio", exc)
                self.state["portfolio"] = {"available": False, "reason": f"portfolio build failed: {type(exc).__name__}"}
        else:  # unconditional (not setdefault) so a stale book never lingers when edges vanish
            self.state["portfolio"] = {"available": False, "reason": "no positive-edge candidates"}

        account = load_account(PAPER_ACCOUNT_PATH, self.bankroll)
        account = update_account(
            account, slate, markets.get("market_prices", {}), datetime.now(timezone.utc).isoformat(),
            size_mode=str(self.config.get("paper_size_mode", "kelly")),
            max_total_exposure_pct=float(self.config.get("max_total_exposure_pct", 0.80)),
            min_stake_usd=float(self.config.get("min_fillable_usd", 5)),
        )
        save_account(account, PAPER_ACCOUNT_PATH)
        self.state["paper_account"] = account
        self.state["execution"] = live_execution_status(
            enabled=bool(self.config.get("use_live_execution", False)), has_credentials=False
        )

        # Rolling equity series for the live sparkline (keep the last ~240 marks).
        eq = account.get("summary", {}).get("equity")
        if eq is not None:
            self._equity_curve.append({"t": _now_iso(), "equity": eq})
            self._equity_curve = self._equity_curve[-240:]
            self.state["equity_curve"] = self._equity_curve

        src = markets.get("source", "?")
        n_act = sum(1 for r in slate if r.get("actionable"))
        eq = account.get("summary", {}).get("equity", 0)
        return f"{src}: {len(slate)} edges, {n_act} actionable · equity ${eq}"

    def job_retrain(self) -> str:
        """Full pipeline refresh: re-train the model + refresh data/backtest/squads, then rebuild context."""
        prev_live = self.state.get("tracker", {}).get("live")  # preserve in-play snapshot across retrain
        state = run_pipeline(refresh=False, n_sims=self.sim_n, history_years=self.history_years)
        state["config"] = self.config
        self.state = state
        # Carry forward the live-maintained sections so the UI doesn't blank for a cycle.
        self.state["equity_curve"] = self._equity_curve
        if prev_live and isinstance(self.state.get("tracker"), dict):
            self.state["tracker"].setdefault("live", prev_live)
        self.ctx = build_context(self.config, history_years=self.history_years)
        self.client = LiveScoreClient.from_config(self.config, self.ctx.normalizer)
        self.normalizer = self.ctx.normalizer
        self._elo = None
        prior = self.state.get("tracker", {}).get("predictions", [])
        self._frozen = frozen_from_predictions(prior)
        self._prev_completed = {
            frozenset((p["home"], p["away"])) for p in prior if p.get("status") == "completed"
        }
        if SIM_OUTCOMES_PATH.exists():
            try:
                self._samples = json.loads(SIM_OUTCOMES_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        model = self.state.get("model", {})
        cal = model.get("calibration", {}).get("calibrated", {})
        return f"retrained {model.get('kind', '?')} · logloss {cal.get('log_loss', '?')}"

    def _build_elo(self):
        ctx = self.ctx
        assert ctx is not None
        wc_live = _live_wc_rows(WC_RESULTS_PATH, self.normalizer, ctx.results_full["date"].max(), existing=ctx.results_full)
        if wc_live is not None and len(wc_live):
            elo_input = pd.concat([ctx.results_full, wc_live], ignore_index=True, sort=False).sort_values("date")
        else:
            elo_input = ctx.results_full
        return EloEngine(EloConfig()).process_matches(elo_input, host_nations=HOST_NATIONS_2026)

    # ----- persistence -----------------------------------------------------------------

    def _attach_sidecars(self) -> None:
        """Fold in the ops watchdog + improver outputs (they're separate processes; the engine
        stays the single writer of dashboard_state.json by reading their small report files)."""
        ops_path = STATE_PATH.parent / "ops_report.json"
        prop_path = STATE_PATH.parent / "improvement_proposals.json"
        try:
            self.state["ops"] = json.loads(ops_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self.state.pop("ops", None)
        try:
            data = json.loads(prop_path.read_text(encoding="utf-8"))
            entries = data.get("entries", [])
            latest = entries[-1] if entries else None
            if latest:  # hide resolved (implemented/denied) so only OPEN proposals show
                resolved = self._resolved_titles()
                latest = dict(latest)
                latest["proposals"] = [p for p in latest.get("proposals", []) if _norm(p.get("title")) not in resolved]
            self.state["proposals"] = {"updated_at": data.get("updated_at"), "latest": latest}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self.state.pop("proposals", None)
        try:
            impl = json.loads((STATE_PATH.parent / "improvements_log.json").read_text(encoding="utf-8"))
            self.state["improvements"] = {"updated_at": impl.get("updated_at"), "entries": (impl.get("entries") or [])[-12:]}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self.state.pop("improvements", None)

    def _resolved_titles(self) -> set:
        """Titles the improver should drop: implemented/denied in the runtime log + Claude's
        git-tracked resolutions (data/manual/resolved_proposals.json)."""
        titles: set = set()
        impl = STATE_PATH.parent / "improvements_log.json"
        seed = STATE_PATH.parent.parent / "manual" / "resolved_proposals.json"
        try:
            for e in json.loads(impl.read_text(encoding="utf-8")).get("entries", []):
                if e.get("status") in ("implemented", "denied"):
                    titles.add(_norm(e.get("title")))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        try:
            for e in json.loads(seed.read_text(encoding="utf-8")).get("resolved", []):
                titles.add(_norm(e.get("title")))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return titles

    def _record_resolutions(self, items: list) -> None:
        """Persist user deny/implement clicks from the web into the runtime log + nudge the improver."""
        path = STATE_PATH.parent / "improvements_log.json"
        try:
            log = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            log = {"entries": []}
        for it in items:
            title = (it or {}).get("title")
            if not title:
                continue
            log.setdefault("entries", []).append(
                {"at": _now_iso(), "title": title, "status": (it.get("status") or "denied"), "by": "user"}
            )
        log["entries"] = log["entries"][-200:]
        log["updated_at"] = _now_iso()
        try:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(log, indent=2), encoding="utf-8")
            tmp.replace(path)
            (STATE_PATH.parent / "improver_now").write_text("1", encoding="utf-8")  # refill now
        except OSError:
            pass

    def _persist(self) -> None:
        self.state["generated_at"] = _now_iso()
        self.state["engine"] = self.status_summary()
        self._attach_sidecars()
        tmp = STATE_PATH.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(self.state, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, STATE_PATH)  # atomic: the web reader never sees a partial file
            self.last_write = time.time()
        except OSError as exc:
            self._log_error("persist", exc)

    def status_summary(self) -> dict:
        now = time.monotonic()
        return {
            "live": True,
            "started_at": datetime.fromtimestamp(self.started_at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "uptime_sec": int(time.time() - self.started_at),
            "sim_n": self.sim_n,
            "jobs": {
                name: {
                    "status": job.last_status,
                    "detail": job.last_detail,
                    "seconds": job.last_seconds,
                    "runs": job.runs,
                    "errors": job.errors,
                    "interval_sec": int(job.interval),
                    "next_in_sec": max(0, int(job.next_due - now)) if job.last_run else 0,
                }
                for name, job in self.jobs.items()
            },
            "recent_errors": self.errors[-5:],
        }
