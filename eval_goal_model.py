"""A/B harness for the goal model: raw vs opponent-adjusted strengths.

Fits each variant on the last 12y before a holdout, then scores its scoreline
distribution on the time-forward holdout: 1X2 log loss (derived from the score
matrix), exact-scoreline log loss, and how *separated* the xG is (stdev of total
xG across matches — the direct measure of "flat xG").

    python eval_goal_model.py
"""
import statistics as st
import sys
from math import log
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from ingest.results import load_results  # noqa: E402
from model.goal_model import PoissonGoalModel  # noqa: E402

RAW = ROOT / "data" / "raw" / "results.csv"
ALI = ROOT / "data" / "manual" / "team_aliases.yaml"


def derive_1x2(dist):
    ph = pd_ = pa = 0.0
    for (h, a), p in dist.items():
        if h > a:
            ph += p
        elif h == a:
            pd_ += p
        else:
            pa += p
    return ph, pd_, pa


def evaluate(adjusted, df, holdout_start, train_years=12):
    start = pd.Timestamp(holdout_start)
    cutoff = start - pd.DateOffset(years=train_years)
    train = df[(df["date"] >= cutoff) & (df["date"] < start)]
    test = df[df["date"] >= start]
    gm = PoissonGoalModel(opponent_adjusted=adjusted).fit(train)
    seen = set(gm.attack_strength)
    n = ll = sll = 0.0
    xgs, home_atts = [], []
    for r in test.itertuples(index=False):
        h, a = r.home_team, r.away_team
        if h not in seen or a not in seen:
            continue
        dist = gm.scoreline_distribution(h, a)
        ph, pd_, pa = derive_1x2(dist)
        hs, as_ = int(r.home_score), int(r.away_score)
        actual = ph if hs > as_ else pd_ if hs == as_ else pa
        ll += -log(max(actual, 1e-9))
        p_exact = dist.get((min(hs, 10), min(as_, 10)), 1e-9)
        sll += -log(max(p_exact, 1e-9))
        hx, ax = gm.expected_goals(h, a)
        xgs.append(hx + ax)
        n += 1
    att_vals = list(gm.attack_strength.values())
    return int(n), ll / n, sll / n, st.pstdev(xgs), st.pstdev(att_vals)


def main():
    df = load_results(RAW, mapping_path=ALI, refresh=False).dropna(subset=["home_score", "away_score"]).sort_values("date")
    print(f"loaded {len(df):,} matches\n")
    print(f"{'holdout':10s} {'variant':14s} {'n':>5s} {'1x2_ll':>8s} {'score_ll':>9s} {'xG_stdev':>9s} {'attack_stdev':>12s}")
    for hold in ("2023-01-01", "2025-01-01"):
        for adj in (False, True):
            n, ll, sll, xgsd, attsd = evaluate(adj, df, hold)
            label = "opp_adjusted" if adj else "raw(current)"
            print(f"{hold[:7]:10s} {label:14s} {n:5d} {ll:8.4f} {sll:9.4f} {xgsd:9.3f} {attsd:12.3f}")
        print()


if __name__ == "__main__":
    main()
