"""Does squad market value add signal over Elo + FIFA rank? Time-forward A/B.

CAVEAT (read before trusting the number): squads_2026.csv holds CURRENT values, and the model
trains on 2014-2026. Applying today's values to old matches is anachronistic, and only ~20 teams
are covered — so this is a directional probe, not the rigorous test. The rigorous version needs
point-in-time historical squad values (FBref/Transfermarkt; Codex's ingestion). We report the
recent holdout + the covered-teams subset, where current values are least wrong.

    python eval_squads.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402
from sklearn.metrics import log_loss  # noqa: E402

from features.build_features import build_match_features, HOST_NATIONS_2026  # noqa: E402
from features.elo import EloConfig, EloEngine  # noqa: E402
from ingest.results import TeamNameNormalizer, load_results  # noqa: E402
from pipeline.orchestrator import (  # noqa: E402
    ALIASES_PATH, RAW_RESULTS_PATH, SQUADS_PATH, WC_RESULTS_PATH,
    _live_wc_rows, _load_fifa_rankings, _select_feature_columns, _to_model_matrix,
)


def _fit_predict(matrix, y, cut):
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0)
    m.fit(matrix[:cut], y[:cut])
    return m.predict_proba(matrix[cut:]), list(m.classes_)


def main():
    norm = TeamNameNormalizer.from_yaml(ALIASES_PATH)
    full = load_results(RAW_RESULTS_PATH, mapping_path=ALIASES_PATH, refresh=False).dropna(subset=["home_score", "away_score"])
    full["home_score"] = full["home_score"].astype(int)
    full["away_score"] = full["away_score"].astype(int)
    recent = full[full["date"] >= full["date"].max() - pd.DateOffset(years=12)].copy()

    live = _live_wc_rows(WC_RESULTS_PATH, norm, full["date"].max())
    elo_input = pd.concat([full, live], ignore_index=True, sort=False) if live is not None else full
    elo = EloEngine(EloConfig()).process_matches(elo_input, host_nations=HOST_NATIONS_2026)
    rankings = _load_fifa_rankings(norm, refresh=False)
    feats = build_match_features(recent, elo, rankings=rankings, host_nations=HOST_NATIONS_2026)
    feat_cols = _select_feature_columns(feats, has_rankings=rankings is not None)

    sq = pd.read_csv(SQUADS_PATH)
    val = {norm.canonical(str(r["team"])): float(r["squad_value_m"]) for _, r in sq.iterrows()}
    feats["squad_val_diff"] = feats["home_team"].map(val).fillna(0.0) - feats["away_team"].map(val).fillna(0.0)
    feats["_both"] = feats["home_team"].isin(val) & feats["away_team"].isin(val)

    labelled = feats.dropna(subset=["target_1x2"]).sort_values("date").reset_index(drop=True)
    y = labelled["target_1x2"].to_numpy()
    labels = sorted(set(y))
    cut = int(len(labelled) * 0.8)
    both = labelled["_both"].to_numpy()[cut:]

    def evaluate(cols):
        matrix = _to_model_matrix(labelled, cols).to_numpy(dtype=float)
        proba, classes = _fit_predict(matrix, y, cut)
        proba = proba[:, [classes.index(c) for c in labels]]  # reorder to sorted labels
        return proba

    base = evaluate(feat_cols)
    squad = evaluate(feat_cols + ["squad_val_diff"])
    yh = y[cut:]

    print(f"production reference: ~0.858 (calibrated)\n")
    print(f"holdout matches: {len(yh):,}  ({recent.iloc[cut]['date'].date() if cut < len(recent) else '?'}+)  "
          f"| both teams covered by squad data: {int(both.sum())}\n")
    print(f"{'slice':22s} {'base (Elo+rank)':>16s} {'+ squad value':>16s} {'delta':>9s}")
    bA, sA = log_loss(yh, base, labels=labels), log_loss(yh, squad, labels=labels)
    print(f"{'ALL holdout':22s} {bA:16.4f} {sA:16.4f} {sA - bA:+9.4f}")
    if both.sum() > 20:
        bC, sC = log_loss(yh[both], base[both], labels=labels), log_loss(yh[both], squad[both], labels=labels)
        print(f"{'covered-teams only':22s} {bC:16.4f} {sC:16.4f} {sC - bC:+9.4f}")
    print("\nLower is better. A negative delta = squad value helped.")


if __name__ == "__main__":
    main()
