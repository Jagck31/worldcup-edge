import unittest

from ingest.results import DEFAULT_ALIASES, TeamNameNormalizer


class ResultsIngestTests(unittest.TestCase):
    def test_team_normalizer_applies_aliases_case_insensitively(self):
        normalizer = TeamNameNormalizer(DEFAULT_ALIASES)

        self.assertEqual(normalizer.canonical("usa"), "United States")
        self.assertEqual(normalizer.canonical(" u.s.a. "), "United States")


if __name__ == "__main__":
    unittest.main()
