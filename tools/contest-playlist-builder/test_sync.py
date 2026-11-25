import json
import unittest
from unittest.mock import Mock

from sync import DIVISIONS, Spotify, SyncError, load_playlist_ids, sync


def history(artist, title, label):
    return [artist, title, *[""] * 8, label]


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.sheets = Mock()
        self.sheets.tabs.return_value = {"2011", "2025", "2012", "notes"}
        self.spotify = Mock()
        self.playlist_ids = {division: f"{index:022d}" for index, division in enumerate(DIVISIONS)}
        self.state = [["Division", "Last row"]] + [
            [division, 2 if division == "All Star" else 1] for division in reversed(DIVISIONS)
        ]
        self.original = [[], [], [], [], [["A", "Old"]], []]
        self.history = [
            [history("A", "Old", "ALL-STARS")],
            [
                history("A", "Old", "All Star Newcomer"),
                history("B", "New (Clean edit)", "All Star"),
                history("C", "Missing", "All-Star"),
            ],
        ]

    def test_check_verifies_saved_prefix_without_writing(self):
        self.sheets.read.side_effect = [self.history, [self.state, *self.original, []]]
        sync(self.sheets, self.spotify, "source", "output", check=True)
        self.sheets.read.assert_any_call("source", ["'2012'!A2:K", "'2025'!A2:K"])
        self.sheets.write.assert_not_called()
        self.spotify.search.assert_not_called()

    def test_changed_prefix_stops_before_writes(self):
        self.original[4] = [["Other", "Old"]]
        self.sheets.read.side_effect = [self.history, [self.state, *self.original, []]]
        with self.assertRaisesRegex(SyncError, "All Star.*saved sheet row 2"):
            sync(self.sheets, self.spotify, "source", "output", playlist_ids=self.playlist_ids)
        self.sheets.write.assert_not_called()
        self.spotify.search.assert_not_called()

    def test_resumes_at_next_row_and_updates_existing_counter(self):
        self.history[1][0][10] = "All Star"
        refreshed = [[], [], [], [], [["A", "Old"], ["B", "New (Clean edit)"], ["C", "Missing"]], []]
        self.sheets.read.side_effect = [self.history, [self.state, *self.original, []], refreshed]
        self.spotify.search.side_effect = ["spotify:track:new", None]
        sync(self.sheets, self.spotify, "source", "output", playlist_ids=self.playlist_ids)
        self.assertEqual([call.args for call in self.spotify.search.call_args_list],
                         [("B", "New (Clean edit)"), ("C", "Missing")])
        self.sheets.write.assert_any_call("output", {"'All Star'!A2:B": refreshed[4]})
        self.spotify.add.assert_called_once_with(self.playlist_ids["All Star"], ["spotify:track:new"])
        self.assertEqual(self.sheets.append_failures.call_args.args[1][0][:3],
                         ["All Star", "C", "Missing"])
        self.sheets.write.assert_called_with("output", {"'Spotify Sync State'!B3": [[4]]})

    def test_append_error_leaves_existing_counter(self):
        self.history[1] = self.history[1][1:2]
        refreshed = [[], [], [], [], [["A", "Old"], ["B", "New (Clean edit)"]], []]
        self.sheets.read.side_effect = [self.history, [self.state, *self.original, []], refreshed]
        self.spotify.add.side_effect = SyncError("Spotify playlist append failed")
        with self.assertRaisesRegex(SyncError, "playlist append"):
            sync(self.sheets, self.spotify, "source", "output", playlist_ids=self.playlist_ids)
        self.sheets.write.assert_called_once_with("output", {"'All Star'!A2:B": refreshed[4]})

    def test_playlist_config_requires_six_distinct_ids(self):
        self.assertEqual(load_playlist_ids(json.dumps(self.playlist_ids)), self.playlist_ids)
        invalid = (None, "{", "[]", "{}", json.dumps(dict(self.playlist_ids, Novice="bad")),
                   json.dumps(dict(self.playlist_ids, Novice=self.playlist_ids["Newcomer"])))
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaisesRegex(SyncError, "SPOTIFY_PLAYLIST_IDS"):
                load_playlist_ids(raw)

    def test_missing_playlist_targets_stops_before_sheets_calls(self):
        with self.assertRaisesRegex(SyncError, "playlist targets are required"):
            sync(self.sheets, self.spotify, "source", "output")
        self.sheets.tabs.assert_not_called()
        self.sheets.write.assert_not_called()

    def test_spotify_search_uses_shared_matcher_and_preserves_version(self):
        session = Mock()
        spotify = Spotify(session)
        spotify.access_token = "test-token"
        session.request.return_value.json.return_value = {
            "tracks": {"items": [
                {"uri": "spotify:track:studio", "name": "Song",
                 "artists": [{"name": "Current Name (Former Name)"}]},
                {"uri": "spotify:track:acoustic", "name": "Song - Acoustic",
                 "artists": [{"name": "Current Name (Former Name)"}]},
            ]},
        }
        self.assertEqual(spotify.search("Former Name", "Song (Acoustic)"),
                         "spotify:track:acoustic")
        method, url = session.request.call_args.args
        self.assertEqual((method, url), ("GET", "https://api.spotify.com/v1/search"))


if __name__ == "__main__":
    unittest.main()
