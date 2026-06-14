"""A/B harness for Elo tuning.

Rebuilds point-in-time Elo under different configs and scores each on a
time-forward holdout using proper scoring rules (binary log loss + Brier on the
home result in {win=1, draw=0.5, loss=0}) plus decisive-match accuracy. This is
exactly what Elo is fit to, so lower log loss / Brier == a better-calibrated rating.

    python eval_elo.py
"""
import re
import sys
from math import log
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from ingest.results import load_results  # noqa: E402

RAW = ROOT / "data" / "raw" / "results.csv"
ALI = ROOT / "data" / "manual" / "team_aliases.yaml"

KNOCK = (
    r"\bknockout\b", r"\bround of\b", r"\bquarter[-\s]?finals?\b",
    r"\bsemi[-\s]?finals?\b", r"^(third[-\s]?place|bronze|final)$",
)
TOURN = ("world cup", "euro", "copa america", "afcon", "asian cup")


def expected(r_team, r_opp):
    return 1.0 / (1.0 + 10 ** ((r_opp - r_team) / 400.0))


def k_factor(tournament, stage, K):
    t, s = str(tournament).lower(), str(stage or "").lower()
    if any(re.search(p, s) for p in KNOCK):
        return K["knock"]
    if any(w in t for w in TOURN):
        return K["tourn"]
    if "qualif" in t or "qualif" in s or "nations league" in t:
        return K["qual"]
    return K["friendly"]


def run(df, home_adv=75.0, autocorr=0.0, k_scale=1.0, holdouts=("2023-01-01", "2025-01-01")):
    K = {"friendly": 10.0 * k_scale, "qual": 25.0 * k_scale, "tourn": 35.0 * k_scale, "knock": 50.0 * k_scale}
    ratings = {}
    starts = [pd.Timestamp(h) for h in holdouts]
    acc = {h: {"n": 0, "brier": 0.0, "ll": 0.0, "correct": 0, "dec": 0} for h in holdouts}
    has_stage = "stage" in df.columns
    for r in df.itertuples(index=False):
        home, away = r.home_team, r.away_team
        rh, ra = ratings.get(home, 1500.0), ratings.get(away, 1500.0)
        adj = 0.0 if bool(getattr(r, "neutral", False)) else home_adv
        eh = expected(rh + adj, ra)
        hsc, asc = int(r.home_score), int(r.away_score)
        res = 1.0 if hsc > asc else 0.5 if hsc == asc else 0.0
        d = pd.Timestamp(r.date)
        for h, start in zip(holdouts, starts):
            if d >= start:
                a = acc[h]
                a["n"] += 1
                a["brier"] += (eh - res) ** 2
                p = min(max(eh, 1e-6), 1 - 1e-6)
                a["ll"] += -(res * log(p) + (1 - res) * log(1 - p))
                if hsc != asc:
                    a["dec"] += 1
                    if (eh > 0.5) == (hsc > asc):
                        a["correct"] += 1
        margin = abs(hsc - asc)
        base = 1.0 if margin <= 1 else 1.0 + log(margin)
        if autocorr > 0 and margin >= 2:
            reff_h, reff_a = rh + adj, ra
            diff = (reff_h - reff_a) if hsc > asc else (reff_a - reff_h)
            base *= 2.2 / (autocorr * diff + 2.2)
        stage = getattr(r, "stage", None) if has_stage else None
        k = k_factor(getattr(r, "tournament", ""), stage, K) * base
        delta = k * (res - eh)
        ratings[home], ratings[away] = rh + delta, ra - delta
    out = {}
    for h in holdouts:
        a = acc[h]
        out[h] = (a["brier"] / a["n"], a["ll"] / a["n"], a["correct"] / a["dec"] if a["dec"] else 0, a["n"])
    return out


def main():
    df = load_results(RAW, mapping_path=ALI, refresh=False)
    df = df.dropna(subset=["home_score", "away_score"]).sort_values("date").reset_index(drop=True)
    print(f"loaded {len(df):,} matches  {df['date'].min().date()} -> {df['date'].max().date()}\n")

    def show(label, cfg):
        res = run(df, **cfg)
        cells = []
        for h, (br, ll, ac, n) in res.items():
            cells.append(f"{h[:4]}+: ll={ll:.4f} brier={br:.4f} acc={ac:.3f} (n={n})")
        print(f"{label:32s} | " + "  |  ".join(cells))

    print("=== BASELINE (current production) ===")
    show("home75 autocorr0 kx1.0", dict(home_adv=75, autocorr=0.0, k_scale=1.0))

    print("\n=== MoV autocorrection sweep (538-style) ===")
    for ac in (0.0005, 0.001, 0.0015, 0.002, 0.0025):
        show(f"autocorr={ac}", dict(home_adv=75, autocorr=ac, k_scale=1.0))

    print("\n=== home-advantage sweep (autocorr=0.001) ===")
    for ha in (55, 65, 75, 85, 100):
        show(f"home_adv={ha}", dict(home_adv=ha, autocorr=0.001, k_scale=1.0))

    print("\n=== K-scale sweep (responsiveness; autocorr=0.001, home=75) ===")
    for ks in (0.7, 0.85, 1.0, 1.2, 1.5):
        show(f"k_scale={ks}", dict(home_adv=75, autocorr=0.001, k_scale=ks))


if __name__ == "__main__":
    main()
