import unittest

from src.ncaa.enrichment import match_player, normalize_height, same_team


class MatchingTests(unittest.TestCase):
    def test_height_conversion_and_unknown_units(self):
        self.assertEqual(normalize_height("6-2"), (187.96, None))
        self.assertEqual(normalize_height("6′ 2″"), (187.96, None))
        self.assertEqual(normalize_height("1.98 m"), (198.0, None))
        self.assertEqual(normalize_height("198 cm"), (198.0, None))
        self.assertEqual(normalize_height(None), (None, None))
        for height in ("6-14", "198", "very tall", "300 cm"):
            self.assertIsNone(normalize_height(height)[0])
            self.assertIsNotNone(normalize_height(height)[1])

    def test_no_name_fallback_for_conflicting_ids(self):
        status, player, _ = match_player({"player_id": "1", "player_name": "Alex"},
            [{"players": [{"player_id": "2", "player_name": "Alex"}]}])
        self.assertEqual(status, "unmatched")
        self.assertIsNone(player)

    def test_ambiguous_names_are_not_joined(self):
        status, player, _ = match_player({"player_name": "Alex"},
            [{"players": [{"player_id": "1", "player_name": "Alex"},
                          {"player_id": "2", "player_name": "Alex"}]}])
        self.assertEqual(status, "ambiguous_name")
        self.assertIsNone(player)

    def test_jersey_conflict_prevents_name_join(self):
        status, _, _ = match_player({"player_name": "Alex", "jersey_number": "1"},
            [{"players": [{"player_name": "Alex", "jersey_number": "2"}]}])
        self.assertEqual(status, "jersey_conflict")

    def test_no_fuzzy_name_or_team_matching(self):
        self.assertFalse(same_team({"team_id": "1", "team_name": "Owls"}, {"team_name": "Owls"}))
        status, _, _ = match_player({"player_name": "Alex Jr."}, [{"players": [{"player_name": "Alex"}]}])
        self.assertEqual(status, "unmatched")
