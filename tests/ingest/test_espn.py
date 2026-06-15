import unittest

from ingest.espn import EspnScoreClient, _classify
from ingest.livescores import LiveEvent, merge_event_lists, _match_minute
from ingest.results import TeamNameNormalizer
from pipeline.orchestrator import ALIASES_PATH


def _raw(home, away, hs, aw, state="post", completed=True, detail="FT", name="STATUS_FULL_TIME", d="2026-06-14T04:00Z"):
    return {
        "id": "1",
        "date": d,
        "competitions": [{
            "status": {"type": {"state": state, "completed": completed, "shortDetail": detail, "name": name}},
            "competitors": [
                {"homeAway": "home", "score": hs, "team": {"displayName": home}},
                {"homeAway": "away", "score": aw, "team": {"displayName": away}},
            ],
        }],
    }


class EspnParseTests(unittest.TestCase):
    def setUp(self):
        self.client = EspnScoreClient.from_config({}, TeamNameNormalizer.from_yaml(ALIASES_PATH))

    def test_finished_event_parses_and_canonicalises(self):
        ev = self.client._to_event(_raw("Australia", "Türkiye", "2", "0"))
        self.assertEqual((ev.home, ev.away), ("Australia", "Turkey"))  # diacritic + alias resolved
        self.assertEqual((ev.home_score, ev.away_score), (2, 0))
        self.assertTrue(ev.finished)

    def test_scheduled_event_has_no_scores(self):
        ev = self.client._to_event(
            _raw("Spain", "Cape Verde", None, None, state="pre", completed=False, detail="Scheduled", name="STATUS_SCHEDULED")
        )
        self.assertEqual(ev.state, "scheduled")
        self.assertIsNone(ev.home_score)

    def test_live_minute_from_espn_displayclock(self):
        raw = _raw("Belgium", "Egypt", "0", "1", state="in", completed=False, detail="44'", name="STATUS_FIRST_HALF")
        raw["competitions"][0]["status"]["displayClock"] = "44'"
        ev = self.client._to_event(raw)
        self.assertEqual(ev.state, "in_play")
        self.assertEqual(ev.minute, "44'")

    def test_classify_states(self):
        self.assertEqual(_classify("post", True, "STATUS_FULL_TIME"), "finished")
        self.assertEqual(_classify("in", False, "STATUS_FIRST_HALF"), "in_play")
        self.assertEqual(_classify("pre", False, "STATUS_SCHEDULED"), "scheduled")
        self.assertEqual(_classify("pre", False, "STATUS_POSTPONED"), "postponed")

    def test_missing_competitor_returns_none(self):
        bad = {"id": "1", "date": "2026-06-14", "competitions": [{"competitors": [{"homeAway": "home", "team": {"displayName": "X"}}]}]}
        self.assertIsNone(self.client._to_event(bad))


class MergeEventListsTests(unittest.TestCase):
    def _ev(self, home, away, hs, aw, state, date="2026-06-14"):
        return LiveEvent("x", date, "", home, away, hs, aw, state.upper(), state)

    def test_more_advanced_state_wins(self):
        sportsdb = [self._ev("Australia", "Turkey", None, None, "scheduled")]  # feed lagging
        espn = [self._ev("Australia", "Turkey", 2, 0, "finished")]             # ESPN has FT
        merged = merge_event_lists(sportsdb, espn)
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0].finished)
        self.assertEqual((merged[0].home_score, merged[0].away_score), (2, 0))

    def test_distinct_games_all_kept(self):
        a = [self._ev("Australia", "Turkey", 2, 0, "finished")]
        b = [self._ev("Netherlands", "Japan", 2, 2, "finished")]  # only ESPN has this one
        self.assertEqual(len(merge_event_lists(a, b)), 2)

    def test_same_pair_different_day_not_collapsed(self):
        group = [self._ev("Spain", "Portugal", 1, 0, "finished", date="2026-06-14")]
        ko = [self._ev("Spain", "Portugal", 0, 2, "finished", date="2026-07-05")]  # knockout rematch
        self.assertEqual(len(merge_event_lists(group, ko)), 2)

    def test_tie_prefers_copy_with_live_minute(self):
        a = LiveEvent("x", "2026-06-15", "", "Belgium", "Egypt", 0, 1, "2H", "in_play", "")
        b = LiveEvent("y", "2026-06-15", "", "Belgium", "Egypt", 0, 1, "2H", "in_play", "67'")
        merged = merge_event_lists([a], [b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].minute, "67'")


class MatchMinuteTests(unittest.TestCase):
    def test_progress_number_becomes_clock(self):
        self.assertEqual(_match_minute("67", "2H"), "67'")

    def test_halftime_and_blank(self):
        self.assertEqual(_match_minute("", "HT"), "HT")
        self.assertEqual(_match_minute("", "NS"), "")  # not live -> no stray clock


if __name__ == "__main__":
    unittest.main()
