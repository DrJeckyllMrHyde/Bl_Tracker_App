import os
import tempfile
import unittest

from bl_tracker import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.temp_dir.name, "test.db"))

    def tearDown(self):
        self.db.conn.close()
        self.temp_dir.cleanup()

    def test_series_episode_and_cast_lifecycle(self):
        series_id = self.db.add_or_update_series(
            None,
            "Série test",
            "Thaïlande",
            3,
            "https://example.com",
            "",
            "Notes",
        )
        self.db.add_person_to_series(series_id, "Prénom", "Nom", "Rôle")

        self.assertEqual(len(self.db.get_episodes(series_id)), 3)
        self.assertEqual(len(self.db.people_for_series(series_id)), 1)

        first_episode = self.db.get_episodes(series_id)[0]
        self.db.update_episode(first_episode["id"], True, "2026-08-16")
        self.assertEqual(self.db.get_episodes(series_id)[0]["seen"], 1)

        self.db.delete_series(series_id)
        self.assertIsNone(self.db.get_series(series_id))
        self.assertEqual(self.db.list_people(), [])


if __name__ == "__main__":
    unittest.main()
