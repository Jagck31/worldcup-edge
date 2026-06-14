import unittest

from ingest.polymarket import (
    PolymarketClient,
    PolymarketMarket,
    build_market_probability_inputs,
    map_world_cup_market,
)
from edge.detect import OrderBook, OrderLevel


KNOWN_TEAMS = ["United States", "Mexico", "Brazil", "France"]


def _market(question: str, outcomes=None, token_ids=None) -> PolymarketMarket:
    return PolymarketMarket(
        market_id="m1",
        question=question,
        slug="",
        outcomes=outcomes if outcomes is not None else ["Yes", "No"],
        token_ids=token_ids if token_ids is not None else ["yes-token", "no-token"],
    )


class PolymarketMappingTests(unittest.TestCase):
    def test_client_fetches_yes_order_book_from_mapping_not_first_token(self):
        class RecordingClient(PolymarketClient):
            def __init__(self):
                super().__init__()
                self.seen_token_id = None

            def get_order_book(self, token_id: str, market_id: str | None = None) -> OrderBook:
                self.seen_token_id = token_id
                return OrderBook(
                    market_id=market_id or token_id,
                    yes_asks=[OrderLevel(price=0.4, size_usd=10)],
                    yes_bids=[],
                )

        mapping = map_world_cup_market(
            _market(
                "Will Brazil win the 2026 FIFA World Cup?",
                outcomes=["No", "Yes"],
                token_ids=["no-token", "yes-token"],
            ),
            known_teams=KNOWN_TEAMS,
        )
        client = RecordingClient()

        book = client.get_yes_order_book(mapping)

        self.assertEqual(client.seen_token_id, "yes-token")
        self.assertEqual(book.market_id, "m1")

    def test_maps_binary_champion_market_with_team_alias_and_yes_no_tokens(self):
        mapping = map_world_cup_market(
            _market("Will USA win the 2026 FIFA World Cup?"),
            known_teams=KNOWN_TEAMS,
        )

        self.assertIsNotNone(mapping)
        self.assertEqual(mapping.market_type, "champion")
        self.assertEqual(mapping.team, "United States")
        self.assertEqual(mapping.probability_column, "p_champion")
        self.assertEqual(mapping.market_name, "Champion - United States")
        self.assertEqual(mapping.yes_token_id, "yes-token")
        self.assertEqual(mapping.no_token_id, "no-token")

    def test_maps_group_winner_market_to_group_probability(self):
        mapping = map_world_cup_market(
            _market("Will Mexico win Group A at the 2026 FIFA World Cup?"),
            known_teams=KNOWN_TEAMS,
        )

        self.assertIsNotNone(mapping)
        self.assertEqual(mapping.market_type, "win_group")
        self.assertEqual(mapping.group, "A")
        self.assertEqual(mapping.probability_column, "p_win_group")
        self.assertEqual(mapping.market_name, "Win Group A - Mexico")

    def test_maps_reach_stage_market_to_simulation_probability_column(self):
        mapping = map_world_cup_market(
            _market("Will United States reach the quarterfinals in the 2026 World Cup?"),
            known_teams=KNOWN_TEAMS,
        )

        self.assertIsNotNone(mapping)
        self.assertEqual(mapping.market_type, "reach_stage")
        self.assertEqual(mapping.probability_column, "p_last_8")
        self.assertEqual(mapping.market_name, "Reach Quarterfinal - United States")

    def test_rejects_non_binary_or_ambiguous_markets(self):
        self.assertIsNone(
            map_world_cup_market(
                _market(
                    "Who will win the 2026 FIFA World Cup?",
                    outcomes=["Brazil", "France"],
                    token_ids=["brazil-token", "france-token"],
                ),
                known_teams=KNOWN_TEAMS,
            )
        )
        self.assertIsNone(
            map_world_cup_market(
                _market("Will Brazil win its opening match at the 2026 World Cup?"),
                known_teams=KNOWN_TEAMS,
            )
        )
        self.assertIsNone(
            map_world_cup_market(
                _market("Will France win the 2025 FIFA Club World Cup?"),
                known_teams=KNOWN_TEAMS,
            )
        )

    def test_rejects_unknown_team_when_known_team_list_is_supplied(self):
        mapping = map_world_cup_market(
            _market("Will Atlantis win the 2026 FIFA World Cup?"),
            known_teams=KNOWN_TEAMS,
        )

        self.assertIsNone(mapping)

    def test_builds_edge_probability_inputs_and_reports_missing_mappings(self):
        mapped = [
            map_world_cup_market(
                _market("Will Brazil win the 2026 FIFA World Cup?"),
                known_teams=KNOWN_TEAMS,
            ),
            map_world_cup_market(
                _market("Will France reach the semifinals in the 2026 World Cup?"),
                known_teams=KNOWN_TEAMS,
            ),
            map_world_cup_market(
                _market("Will Mexico win Group B at the 2026 FIFA World Cup?"),
                known_teams=KNOWN_TEAMS,
            ),
        ]
        probabilities, missing = build_market_probability_inputs(
            [item for item in mapped if item is not None],
            [
                {"team": "Brazil", "p_champion": 0.18, "p_last_4": 0.30},
                {"team": "France", "p_champion": 0.15, "p_last_4": 0.28},
            ],
        )

        self.assertEqual(probabilities["Champion - Brazil"], 0.18)
        self.assertEqual(probabilities["Reach Semifinal - France"], 0.28)
        self.assertEqual(
            missing,
            ["Win Group B - Mexico: missing simulation row for Mexico"],
        )

    def test_probability_input_builder_reports_missing_probability_column(self):
        mapping = map_world_cup_market(
            _market("Will United States reach the quarterfinals in the 2026 World Cup?"),
            known_teams=KNOWN_TEAMS,
        )

        probabilities, missing = build_market_probability_inputs(
            [mapping],
            [{"team": "United States", "p_champion": 0.02}],
        )

        self.assertEqual(probabilities, {})
        self.assertEqual(
            missing,
            ["Reach Quarterfinal - United States: missing probability column p_last_8"],
        )

    def test_probability_input_builder_rejects_out_of_range_probability_values(self):
        mapping = map_world_cup_market(
            _market("Will Brazil win the 2026 FIFA World Cup?"),
            known_teams=KNOWN_TEAMS,
        )

        probabilities, missing = build_market_probability_inputs(
            [mapping],
            [{"team": "Brazil", "p_champion": 1.25}],
        )

        self.assertEqual(probabilities, {})
        self.assertEqual(
            missing,
            ["Champion - Brazil: invalid probability value for p_champion"],
        )


if __name__ == "__main__":
    unittest.main()
