from itertools import combinations
from pathlib import Path
import unittest

from simulate.monte_carlo import (
    GROUPS_2026,
    THIRD_PLACE_SLOTS,
    assign_third_place_slots,
    load_third_place_assignment_table,
)


class ThirdPlaceAssignmentTableTests(unittest.TestCase):
    def test_manual_annex_c_table_covers_all_eight_group_combinations(self):
        table_path = Path("data/manual/third_place_assignments.csv")

        table = load_third_place_assignment_table(table_path)

        expected_keys = {"".join(combo) for combo in combinations(GROUPS_2026, 8)}
        self.assertEqual(set(table), expected_keys)
        self.assertEqual(len(table), 495)
        for mapping in table.values():
            self.assertEqual(set(mapping), set(THIRD_PLACE_SLOTS))
            self.assertTrue(all(value in GROUPS_2026 for value in mapping.values()))

    def test_manual_annex_c_table_matches_known_first_combination(self):
        table_path = Path("data/manual/third_place_assignments.csv")

        table = load_third_place_assignment_table(table_path)

        self.assertEqual(
            table["EFGHIJKL"],
            {"1A": "E", "1B": "J", "1D": "I", "1E": "F", "1G": "H", "1I": "G", "1K": "L", "1L": "K"},
        )
        assigned = assign_third_place_slots(list("EFGHIJKL"), table)
        self.assertEqual(assigned["1A"], "3E")
        self.assertEqual(assigned["1L"], "3K")


if __name__ == "__main__":
    unittest.main()
