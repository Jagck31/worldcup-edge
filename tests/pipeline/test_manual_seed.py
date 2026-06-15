import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ingest.results import TeamNameNormalizer
from pipeline.live_tracker import load_manual_seed_events, merge_finished_into_csv
from pipeline.orchestrator import ALIASES_PATH


class ManualSeedTests(unittest.TestCase):
    def setUp(self):
        self.norm = TeamNameNormalizer.from_yaml(ALIASES_PATH)

    def _seed_csv(self, text: str) -> Path:
        path = Path(tempfile.mkdtemp()) / "manual_results_seed.csv"
        path.write_text(text, encoding="utf-8")
        return path

    def test_loads_valid_rows_and_skips_blank_scores(self):
        path = self._seed_csv(
            "date,home_team,away_team,home_score,away_score\n"
            "2026-06-14,Australia,Turkiye,2,1\n"
            "2026-06-15,Sweden,Tunisia,,\n"  # not played yet -> skipped
        )
        events = load_manual_seed_events(path, self.norm)
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertTrue(e.finished)
        self.assertEqual((e.home_score, e.away_score), (2, 1))
        self.assertEqual(e.state, "finished")

    def test_missing_file_and_header_only_are_harmless(self):
        self.assertEqual(load_manual_seed_events(Path("does_not_exist.csv"), self.norm), [])
        path = self._seed_csv("date,home_team,away_team,home_score,away_score\n")
        self.assertEqual(load_manual_seed_events(path, self.norm), [])

    def test_fill_only_adds_missing_but_never_overwrites_feed(self):
        d = Path(tempfile.mkdtemp())
        results = d / "wc2026_results.csv"
        # Feed already recorded Australia-Turkey 3-0 (canonical spelling, as the engine stores it).
        pd.DataFrame(
            [{"date": "2026-06-14", "home_team": "Australia", "away_team": "Turkey",
              "home_score": 3, "away_score": 0, "home_xg": pd.NA, "away_xg": pd.NA}]
        ).to_csv(results, index=False)
        # Seed uses the schedule's "Turkiye" spelling -> canonicalised to "Turkey" on load, so it
        # must MATCH the existing feed row (and be skipped), not append a duplicate.
        seed_csv = d / "seed.csv"
        seed_csv.write_text(
            "date,home_team,away_team,home_score,away_score\n"
            "2026-06-14,Australia,Turkiye,9,9\n"     # already have it from the feed -> ignored
            "2026-06-14,Netherlands,Japan,1,0\n",    # feed never had it -> appended
            encoding="utf-8",
        )
        seed = load_manual_seed_events(seed_csv, self.norm)
        deltas = merge_finished_into_csv(seed, results, self.norm, fill_only=True)
        out = pd.read_csv(results)

        # The pre-existing feed result is untouched (NOT overwritten by the 9-9 seed row).
        aus = out[(out.home_team == "Australia") & (out.away_team == "Turkey")].iloc[0]
        self.assertEqual((int(aus.home_score), int(aus.away_score)), (3, 0))
        # The game the feed lacked was appended exactly once.
        self.assertEqual(len(deltas["new"]), 1)
        self.assertEqual(int(((out.home_team == "Netherlands") & (out.away_team == "Japan")).sum()), 1)


if __name__ == "__main__":
    unittest.main()
