import random
import unittest

import pandas as pd

from simulate.monte_carlo import (
    GROUPS_2026,
    GroupStanding,
    TournamentSimulator,
    rank_group,
    select_knockout_qualifiers,
)


def _round_robin_fixtures(teams):
    pairings = [(0, 1), (2, 3), (0, 2), (1, 3), (0, 3), (1, 2)]
    rows = [
        {"home_team": teams[a], "away_team": teams[b], "home_score": pd.NA, "away_score": pd.NA}
        for a, b in pairings
    ]
    return pd.DataFrame(rows)


def _demo_groups():
    groups = {}
    for group in GROUPS_2026:
        teams = [f"{group}{i}" for i in range(1, 5)]
        groups[group] = {"teams": teams, "fixtures": _round_robin_fixtures(teams)}
    return groups


class QualificationTests(unittest.TestCase):
    def test_rank_group_orders_by_points_goal_difference_goals_for(self):
        standings = [
            GroupStanding(
                "A", "Alpha", played=3, wins=2, draws=0, losses=1, goals_for=4, goals_against=2
            ),
            GroupStanding(
                "A", "Beta", played=3, wins=2, draws=0, losses=1, goals_for=5, goals_against=4
            ),
            GroupStanding(
                "A", "Gamma", played=3, wins=1, draws=0, losses=2, goals_for=8, goals_against=8
            ),
            GroupStanding(
                "A", "Delta", played=3, wins=1, draws=0, losses=2, goals_for=2, goals_against=5
            ),
        ]

        ranked = rank_group(standings)

        self.assertEqual([team.team for team in ranked], ["Alpha", "Beta", "Gamma", "Delta"])

    def test_select_knockout_qualifiers_takes_top_two_plus_best_eight_thirds(self):
        groups = {}
        for idx, group in enumerate("ABCDEFGHIJKL"):
            groups[group] = [
                GroupStanding(group, f"{group}1", 3, 3, 0, 0, 6, 1),
                GroupStanding(group, f"{group}2", 3, 2, 0, 1, 4, 2),
                GroupStanding(group, f"{group}3", 3, 1, 0, 2, 3 + idx, 4),
                GroupStanding(group, f"{group}4", 3, 0, 0, 3, 1, 7),
            ]

        qualifiers = select_knockout_qualifiers(groups)

        qualified_teams = {entry.team for entry in qualifiers}
        self.assertEqual(len(qualifiers), 32)
        self.assertNotIn("A3", qualified_teams)
        self.assertNotIn("B3", qualified_teams)
        self.assertNotIn("C3", qualified_teams)
        self.assertNotIn("D3", qualified_teams)
        self.assertIn("L3", qualified_teams)

    def test_rank_group_breaks_level_ties_with_head_to_head(self):
        standings = [
            GroupStanding("A", "X", played=3, wins=2, draws=0, losses=1, goals_for=4, goals_against=3),
            GroupStanding("A", "Y", played=3, wins=2, draws=0, losses=1, goals_for=4, goals_against=3),
        ]
        # X beat Y head-to-head, so X must rank first despite identical overall lines.
        results = pd.DataFrame(
            [{"home_team": "X", "away_team": "Y", "home_score": 2, "away_score": 0}]
        )
        ranked = rank_group(standings, results=results, rng=random.Random(0))
        self.assertEqual([s.team for s in ranked], ["X", "Y"])


class SimulationEngineTests(unittest.TestCase):
    def test_simulate_many_produces_coherent_submarket_probabilities(self):
        # Group stage is deterministic (lower-indexed team wins); knockout is a coin flip.
        def sampler(home, away):
            return (2, 0) if home[-1] < away[-1] else (0, 2)

        simulator = TournamentSimulator(
            scoreline_sampler=sampler,
            knockout_win_probability=lambda a, b: 0.5,
            rng=random.Random(7),
        )
        table = simulator.simulate_many(_demo_groups(), n_sims=200)

        self.assertEqual(len(table), 48)
        # Exactly one champion, two finalists, 32 advancing teams per tournament.
        self.assertAlmostEqual(table["p_champion"].sum(), 1.0, places=6)
        self.assertAlmostEqual(table["p_finalist"].sum(), 2.0, places=6)
        self.assertAlmostEqual(table["p_advanced"].sum(), 32.0, places=6)
        self.assertAlmostEqual(table["p_win_group"].sum(), 12.0, places=6)
        # Survival is monotone: champion <= finalist <= ... <= advanced for every team.
        for _, row in table.iterrows():
            self.assertLessEqual(row["p_champion"], row["p_finalist"] + 1e-9)
            self.assertLessEqual(row["p_finalist"], row["p_last_4"] + 1e-9)
            self.assertLessEqual(row["p_last_4"], row["p_last_8"] + 1e-9)
            self.assertLessEqual(row["p_last_8"], row["p_last_16"] + 1e-9)
            self.assertLessEqual(row["p_last_16"], row["p_advanced"] + 1e-9)


if __name__ == "__main__":
    unittest.main()
