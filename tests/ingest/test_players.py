import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from ingest.players import (
    build_squad_strength_table,
    fetch_fbref_player_stats,
    normalize_fbref_player_stats,
    normalize_transfermarkt_absences,
    normalize_transfermarkt_market_values,
    parse_transfermarkt_market_values_html,
)


class PlayerIngestTests(unittest.TestCase):
    def test_squad_strength_uses_only_player_stats_known_by_match_date(self):
        team_dates = pd.DataFrame([{"team": "Brazil", "date": "2026-06-15"}])
        squad = pd.DataFrame(
            [
                {"team": "Brazil", "player": "Forward A", "as_of_date": "2026-06-01"},
                {"team": "Brazil", "player": "Midfielder B", "as_of_date": "2026-06-01"},
            ]
        )
        player_stats = pd.DataFrame(
            [
                {
                    "team": "Brazil",
                    "player": "Forward A",
                    "stat_date": "2025-06-30",
                    "minutes": 900,
                    "xg_per90": 0.20,
                    "xa_per90": 0.10,
                },
                {
                    "team": "Brazil",
                    "player": "Forward A",
                    "stat_date": "2026-07-01",
                    "minutes": 900,
                    "xg_per90": 2.00,
                    "xa_per90": 1.00,
                },
                {
                    "team": "Brazil",
                    "player": "Midfielder B",
                    "stat_date": "2026-05-01",
                    "minutes": 450,
                    "xg_per90": 0.05,
                    "xa_per90": 0.05,
                },
            ]
        )

        table = build_squad_strength_table(team_dates, player_stats, squad_members=squad)

        self.assertEqual(table.loc[0, "team"], "Brazil")
        self.assertEqual(str(table.loc[0, "date"].date()), "2026-06-15")
        self.assertAlmostEqual(table.loc[0, "squad_strength"], 0.35)
        self.assertEqual(table.loc[0, "squad_minutes"], 1350)
        self.assertEqual(table.loc[0, "squad_contributors"], 2)

    def test_squad_availability_uses_active_absences_and_latest_prior_market_values(self):
        team_dates = pd.DataFrame([{"team": "France", "date": "2026-06-15"}])
        squad = pd.DataFrame(
            [
                {"team": "France", "player": "Star A", "as_of_date": "2026-06-01"},
                {"team": "France", "player": "Starter B", "as_of_date": "2026-06-01"},
            ]
        )
        player_stats = pd.DataFrame(
            [
                {
                    "team": "France",
                    "player": "Star A",
                    "stat_date": "2026-05-30",
                    "minutes": 900,
                    "xg_per90": 0.50,
                    "xa_per90": 0.20,
                },
                {
                    "team": "France",
                    "player": "Starter B",
                    "stat_date": "2026-05-30",
                    "minutes": 900,
                    "xg_per90": 0.10,
                    "xa_per90": 0.10,
                },
            ]
        )
        market_values = pd.DataFrame(
            [
                {"team": "France", "player": "Star A", "date": "2026-05-01", "market_value_eur": 100_000_000},
                {"team": "France", "player": "Starter B", "date": "2026-05-01", "market_value_eur": 50_000_000},
                {"team": "France", "player": "Starter B", "date": "2026-07-01", "market_value_eur": 200_000_000},
            ]
        )
        absences = pd.DataFrame(
            [
                {
                    "team": "France",
                    "player": "Star A",
                    "as_of_date": "2026-06-01",
                    "unavailable_from": "2026-06-01",
                    "unavailable_until": "2026-06-20",
                    "reason": "hamstring",
                },
                {
                    "team": "France",
                    "player": "Starter B",
                    "as_of_date": "2026-07-01",
                    "unavailable_from": "2026-07-01",
                    "unavailable_until": "2026-07-20",
                    "reason": "future injury",
                },
            ]
        )

        table = build_squad_strength_table(
            team_dates,
            player_stats,
            squad_members=squad,
            market_values=market_values,
            absences=absences,
        )

        self.assertEqual(table.loc[0, "unavailable_players"], 1)
        self.assertEqual(table.loc[0, "key_players_out_value_eur"], 100_000_000)
        self.assertEqual(table.loc[0, "squad_market_value_eur"], 150_000_000)
        self.assertAlmostEqual(table.loc[0, "squad_availability"], 1.0 / 3.0)

    def test_normalizers_accept_common_fbref_and_transfermarkt_columns(self):
        fbref = normalize_fbref_player_stats(
            pd.DataFrame(
                [
                    {
                        "Player": "Player A",
                        "Nation": "Brazil",
                        "Squad": "Club X",
                        "Season_End": "2026-05-31",
                        "Min": "1,234",
                        "xG/90": 0.31,
                        "xAG/90": 0.22,
                    }
                ]
            )
        )
        values = normalize_transfermarkt_market_values(
            pd.DataFrame(
                [
                    {
                        "Name": "Player A",
                        "National Team": "Brazil",
                        "Snapshot Date": "2026-05-01",
                        "Market Value": "€75.5m",
                    }
                ]
            )
        )

        self.assertEqual(fbref.loc[0, "player"], "Player A")
        self.assertEqual(fbref.loc[0, "team"], "Brazil")
        self.assertEqual(fbref.loc[0, "club"], "Club X")
        self.assertEqual(fbref.loc[0, "minutes"], 1234)
        self.assertAlmostEqual(fbref.loc[0, "xg_per90"], 0.31)
        self.assertAlmostEqual(fbref.loc[0, "xa_per90"], 0.22)
        self.assertEqual(values.loc[0, "market_value_eur"], 75_500_000)

    def test_absence_normalizer_accepts_common_transfermarkt_columns(self):
        absences = normalize_transfermarkt_absences(
            pd.DataFrame(
                [
                    {
                        "Player": "Player A",
                        "National Team": "Brazil",
                        "Reported": "2026-05-20",
                        "From": "2026-05-18",
                        "Until": "2026-06-30",
                        "Reason": "Knee injury",
                    }
                ]
            )
        )

        self.assertEqual(absences.loc[0, "player"], "Player A")
        self.assertEqual(absences.loc[0, "team"], "Brazil")
        self.assertEqual(str(absences.loc[0, "as_of_date"].date()), "2026-05-20")
        self.assertEqual(str(absences.loc[0, "unavailable_from"].date()), "2026-05-18")
        self.assertEqual(str(absences.loc[0, "unavailable_until"].date()), "2026-06-30")

    def test_fetch_fbref_player_stats_uses_soccerdata_reader(self):
        calls = {}

        class FakeFBref:
            def __init__(self, leagues, seasons):
                calls["leagues"] = leagues
                calls["seasons"] = seasons

            def read_player_season_stats(self, stat_type):
                calls["stat_type"] = stat_type
                return pd.DataFrame([{"Player": "Player A"}])

        fake_soccerdata = SimpleNamespace(FBref=FakeFBref)

        with patch.dict("sys.modules", {"soccerdata": fake_soccerdata}):
            frame = fetch_fbref_player_stats(["2025-2026"], leagues=["Big 5"], stat_type="standard")

        self.assertEqual(calls, {"leagues": ["Big 5"], "seasons": ["2025-2026"], "stat_type": "standard"})
        self.assertEqual(frame.loc[0, "Player"], "Player A")

    def test_parse_transfermarkt_market_values_html(self):
        html = """
        <table>
          <tr><th>Player</th><th>Market value</th></tr>
          <tr><td>Player A</td><td>€75.5m</td></tr>
          <tr><td>Player B</td><td>€900k</td></tr>
        </table>
        """

        frame = parse_transfermarkt_market_values_html(
            html,
            team="Brazil",
            snapshot_date="2026-05-01",
        )

        self.assertEqual(frame["player"].tolist(), ["Player A", "Player B"])
        self.assertEqual(frame["team"].tolist(), ["Brazil", "Brazil"])
        self.assertEqual(frame["market_value_eur"].tolist(), [75_500_000, 900_000])


if __name__ == "__main__":
    unittest.main()
