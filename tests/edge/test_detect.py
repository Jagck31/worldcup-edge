import unittest

from edge.detect import (
    OrderLevel,
    OrderBook,
    detect_edges,
    detect_no_edge,
    detect_yes_edge,
    executable_no_price,
    executable_yes_price,
)


class DetectTests(unittest.TestCase):
    def test_executable_yes_price_uses_weighted_asks_not_midpoint(self):
        book = OrderBook(
            market_id="m1",
            yes_asks=[OrderLevel(price=0.52, size_usd=5), OrderLevel(price=0.55, size_usd=10)],
            yes_bids=[OrderLevel(price=0.48, size_usd=100)],
        )

        executable = executable_yes_price(book, target_usd=10)

        self.assertEqual(round(executable.average_price, 3), 0.535)
        self.assertEqual(executable.fillable_usd, 10.0)

    def test_executable_yes_price_is_share_weighted_for_true_fill_price(self):
        book = OrderBook(
            market_id="wide_book",
            yes_asks=[OrderLevel(price=0.25, size_usd=5), OrderLevel(price=0.75, size_usd=5)],
            yes_bids=[OrderLevel(price=0.20, size_usd=100)],
        )

        executable = executable_yes_price(book, target_usd=10)

        self.assertEqual(executable.average_price, 0.375)
        self.assertEqual(executable.fillable_usd, 10.0)

    def test_detect_yes_edge_requires_threshold_after_fees_and_liquidity(self):
        book = OrderBook(
            market_id="advance_usa",
            yes_asks=[OrderLevel(price=0.40, size_usd=25)],
            yes_bids=[OrderLevel(price=0.35, size_usd=25)],
        )

        edge = detect_yes_edge(
            market_name="USA advance",
            model_probability=0.50,
            book=book,
            target_usd=10,
            min_edge_pp=5.0,
            fees_bps=100,
        )

        self.assertIsNotNone(edge)
        self.assertEqual(edge.executable_price, 0.404)
        self.assertEqual(round(edge.edge_pp, 1), 9.6)
        self.assertEqual(edge.fillable_usd, 10.0)

    def test_executable_no_price_uses_yes_bids_as_implicit_no_asks(self):
        book = OrderBook(
            market_id="fade_team",
            yes_asks=[OrderLevel(price=0.75, size_usd=20)],
            yes_bids=[OrderLevel(price=0.70, size_usd=7), OrderLevel(price=0.60, size_usd=6)],
        )

        executable = executable_no_price(book, target_usd=7)

        self.assertEqual(executable.average_price, 0.35)
        self.assertEqual(executable.fillable_usd, 7.0)
        self.assertEqual(executable.levels_used, 2)

    def test_detect_no_edge_compares_no_probability_to_implicit_no_price(self):
        book = OrderBook(
            market_id="miss_knockouts",
            yes_asks=[OrderLevel(price=0.55, size_usd=25)],
            yes_bids=[OrderLevel(price=0.48, size_usd=25)],
        )

        edge = detect_no_edge(
            market_name="Team misses knockouts",
            model_yes_probability=0.35,
            book=book,
            target_usd=10,
            min_edge_pp=5.0,
            fees_bps=0,
        )

        self.assertIsNotNone(edge)
        self.assertEqual(edge.side, "NO")
        self.assertEqual(edge.model_probability, 0.65)
        self.assertEqual(edge.executable_price, 0.52)
        self.assertEqual(round(edge.edge_pp, 1), 13.0)

    def test_detect_edges_can_include_no_side_without_changing_default_yes_only_behavior(self):
        books = {
            "Team advances": OrderBook(
                market_id="advance",
                yes_asks=[OrderLevel(price=0.55, size_usd=25)],
                yes_bids=[OrderLevel(price=0.48, size_usd=25)],
            )
        }

        default_edges = detect_edges({"Team advances": 0.35}, books, 10, 5.0)
        both_sides = detect_edges({"Team advances": 0.35}, books, 10, 5.0, include_no=True)

        self.assertEqual(default_edges, [])
        self.assertEqual(len(both_sides), 1)
        self.assertEqual(both_sides[0].side, "NO")


if __name__ == "__main__":
    unittest.main()
