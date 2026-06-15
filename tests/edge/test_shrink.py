import unittest

from edge.shrink import blend_toward_market


class BlendTowardMarketTests(unittest.TestCase):
    def test_zero_weight_is_noop(self):
        model = {"a": 0.9, "b": 0.1}
        mids = {"a": 0.5, "b": 0.5}
        out = blend_toward_market(model, mids, [["a", "b"]], weight=0.0)
        self.assertEqual(out, model)

    def test_full_weight_defers_to_devigged_market(self):
        # Two-team partition: raw mids 0.6/0.6 sum to 1.2 (vig) -> de-vig to 0.5/0.5.
        model = {"a": 0.9, "b": 0.1}
        mids = {"a": 0.6, "b": 0.6}
        out = blend_toward_market(model, mids, [["a", "b"]], weight=1.0)
        self.assertAlmostEqual(out["a"], 0.5, places=4)
        self.assertAlmostEqual(out["b"], 0.5, places=4)

    def test_partial_blend_moves_toward_market(self):
        model = {"x": 0.94}  # over-concentrated favourite
        mids = {"x": 0.73, "y": 0.30}  # not a registered partition member -> own mid for x
        out = blend_toward_market(model, mids, partitions=[], weight=0.5)
        # 0.5*0.94 + 0.5*0.73 = 0.835
        self.assertAlmostEqual(out["x"], 0.835, places=3)

    def test_extreme_disagreement_is_pulled_up(self):
        # The real failure mode: model says ~1% to win group, market says ~28%.
        model = {"swe": 0.009, "fav": 0.5, "mid": 0.21, "oth": 0.281}
        mids = {"swe": 0.28, "fav": 0.45, "mid": 0.25, "oth": 0.30}
        out = blend_toward_market(model, mids, [list(model)], weight=0.35)
        self.assertGreater(out["swe"], model["swe"])      # long-shot lifted off the floor
        self.assertLess(out["swe"], mids["swe"])           # but not all the way to market
        self.assertLess(out["fav"], model["fav"])          # over-confident favourite trimmed

    def test_missing_mid_left_unchanged(self):
        model = {"a": 0.8, "b": 0.2}
        mids = {"a": 0.5}  # b has no book
        out = blend_toward_market(model, mids, [["a", "b"]], weight=0.5)
        self.assertEqual(out["b"], 0.2)  # untouched, never silently zeroed

    def test_result_clamped_to_unit_interval(self):
        model = {"a": 1.0}
        mids = {"a": 0.0001}
        out = blend_toward_market(model, mids, partitions=[], weight=0.5)
        self.assertTrue(0.0 < out["a"] < 1.0)

    def test_devig_only_with_two_or_more_members(self):
        # A single-member "partition" is not normalised; it blends toward its own mid.
        model = {"solo": 0.9}
        mids = {"solo": 0.6}
        out = blend_toward_market(model, mids, [["solo"]], weight=1.0)
        self.assertAlmostEqual(out["solo"], 0.6, places=4)


if __name__ == "__main__":
    unittest.main()
