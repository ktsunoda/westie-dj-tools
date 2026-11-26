import json
import unittest
from unittest.mock import Mock

from sync import DIVISIONS, Spotify, SyncError, load_playlist_ids, matching_duration, source_lists, sync


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
        self.headers = [[["Artist", "Title", *[""] * 8, "Competition"]]] * 2
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
        self.sheets.read.side_effect = [self.headers, self.history, [self.state, *self.original, []]]
        sync(self.sheets, self.spotify, "source", "output", check=True)
        self.sheets.read.assert_any_call("source", ["'2012'!A2:K", "'2025'!A2:K"])
        self.sheets.write.assert_not_called()
        self.spotify.search.assert_not_called()

    def test_source_uses_competition_header_on_each_year_tab(self):
        self.sheets.tabs.return_value = {"2025", "2026"}
        older = ["Artist", "Title", *[""] * 8, "Competition"]
        newer = ["Artist", "Title", *[""] * 7, " Competition ", "Spotify"]
        self.sheets.read.side_effect = [
            [[older], [newer]],
            [[history("A", "Old", "Novice")],
             [["B", "New", *[""] * 7, "Novice", "Newcomer"]]],
        ]
        lists, labels = source_lists(self.sheets, "source")
        self.assertEqual(lists["Novice"], [["A", "Old"], ["B", "New"]])
        self.assertEqual(lists["Newcomer"], [])
        self.assertEqual(labels, ["Novice"])
        self.sheets.read.assert_called_with("source", ["'2025'!A2:K", "'2026'!A2:J"])

    def test_source_uses_displayed_text_for_time_formatted_title(self):
        self.sheets.tabs.return_value = {"2026"}
        header = ["Artist", "Title", *[""] * 7, "Competition", "Spotify"]
        self.sheets.read.side_effect = [
            [[header]],
            [[["Matt Wertz", 0.22152777777777777, *[""] * 7, "Novice"]]],
            [[["5:19"]]],
        ]
        lists, _ = source_lists(self.sheets, "source")
        self.assertEqual(lists["Novice"], [["Matt Wertz", "5:19"]])
        self.sheets.read.assert_called_with(
            "source", ["'2026'!B2"], render_option="FORMATTED_VALUE",
        )

    def test_sync_passes_history_duration_without_changing_output_rows(self):
        for columns in self.headers:
            columns[0][2] = "Duration"
        self.history[1][1][2] = "3:26"
        self.history[1][2][2] = 210 / 86400
        refreshed = [[], [], [], [], [["A", "Old"], ["B", "New (Clean edit)"], ["C", "Missing"]], []]
        self.history[1][0][10] = "All Star"
        self.sheets.read.side_effect = [self.headers, self.history, [self.state, *self.original, []], refreshed]
        self.spotify.search.side_effect = ["spotify:track:new", None]
        sync(self.sheets, self.spotify, "source", "output", playlist_ids=self.playlist_ids)
        self.assertEqual([call.kwargs["duration_ms"] for call in self.spotify.search.call_args_list],
                         [206000, 210000])
        self.sheets.write.assert_any_call("output", {"'All Star'!A2:B": refreshed[4]})

    def test_matching_duration_allows_rounding_but_rejects_different_edits(self):
        self.assertEqual(matching_duration({211000, 212000}), 211500)
        self.assertIsNone(matching_duration({211000, 230000}))
        self.assertIsNone(matching_duration(set()))

    def test_missing_competition_header_fails_before_output_writes(self):
        self.sheets.tabs.return_value = {"2026"}
        self.sheets.read.return_value = [[["Artist", "Title", "Spotify"]]]
        with self.assertRaisesRegex(SyncError, "2026.*Competition header"):
            sync(self.sheets, self.spotify, "source", "output", playlist_ids=self.playlist_ids)
        self.sheets.write.assert_not_called()

    def test_changed_prefix_stops_before_writes(self):
        self.original[4] = [["Other", "Old"]]
        self.sheets.read.side_effect = [self.headers, self.history, [self.state, *self.original, []]]
        with self.assertRaisesRegex(SyncError, "All Star.*saved sheet row 2"):
            sync(self.sheets, self.spotify, "source", "output", playlist_ids=self.playlist_ids)
        self.sheets.write.assert_not_called()
        self.spotify.search.assert_not_called()

    def test_resumes_at_next_row_and_updates_existing_counter(self):
        self.history[1][0][10] = "All Star"
        refreshed = [[], [], [], [], [["A", "Old"], ["B", "New (Clean edit)"], ["C", "Missing"]], []]
        self.sheets.read.side_effect = [self.headers, self.history, [self.state, *self.original, []], refreshed]
        self.spotify.search.side_effect = ["spotify:track:new", None]
        sync(self.sheets, self.spotify, "source", "output", playlist_ids=self.playlist_ids)
        self.assertEqual([call.args for call in self.spotify.search.call_args_list],
                         [("B", "New (Clean edit)"), ("C", "Missing")])
        self.sheets.write.assert_any_call("output", {"'All Star'!A2:B": refreshed[4]})
        self.spotify.add.assert_called_once_with(self.playlist_ids["All Star"], ["spotify:track:new"])
        self.assertEqual(self.sheets.append_failures.call_args.args[1][0][:3],
                         ["All Star", "C", "Missing"])
        self.sheets.write.assert_called_with("output", {"'Spotify Sync State'!B3": [[4]]})

    def test_checks_shared_overrides_only_for_spotify_misses(self):
        self.history[1][0][10] = "All Star"
        refreshed = [[], [], [], [], [["A", "Old"], ["B", "New (Clean edit)"], ["C", "Missing"]], []]
        self.sheets.read.side_effect = [self.headers, self.history, [self.state, *self.original], refreshed]
        self.spotify.search.side_effect = ["spotify:track:new", None]
        overrides = Mock()
        overrides.resolve_miss.return_value = "spotify:track:override"

        sync(self.sheets, self.spotify, "source", "output", playlist_ids=self.playlist_ids,
             overrides=overrides)

        overrides.resolve_miss.assert_called_once_with("C", "Missing")
        self.spotify.add.assert_called_once_with(
            self.playlist_ids["All Star"], ["spotify:track:new", "spotify:track:override"],
        )
        self.sheets.append_failures.assert_not_called()

    def test_append_error_leaves_existing_counter(self):
        self.history[1] = self.history[1][1:2]
        refreshed = [[], [], [], [], [["A", "Old"], ["B", "New (Clean edit)"]], []]
        self.sheets.read.side_effect = [self.headers, self.history, [self.state, *self.original, []], refreshed]
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

    def test_legacy_search_is_opt_in_and_preserves_existing_match(self):
        session = Mock()
        spotify = Spotify(session, legacy_fallback=True)
        spotify.access_token = "test-token"
        session.request.return_value.json.return_value = {"tracks": {"items": [
            {"uri": "spotify:track:correct", "name": "Song (Clean Edit)",
             "artists": [{"name": "Artist"}], "explicit": False},
        ]}}
        self.assertEqual(spotify.search("Artist", "Song (Clean Edit)"), "spotify:track:correct")
        session.request.assert_called_once()

        session.reset_mock()
        session.request.return_value.json.return_value = {"tracks": {"items": [
            {"uri": "spotify:track:album", "name": "Song",
             "artists": [{"name": "Artist"}]},
        ]}}
        strict = Spotify(session)
        strict.access_token = "test-token"
        self.assertIsNone(strict.search("Artist", "Song (Clean Edit)"))
        with self.assertLogs(level="INFO"):
            self.assertEqual(spotify.search("Artist", "Song (Clean Edit)"), "spotify:track:album")
        self.assertEqual(session.request.call_args.kwargs["params"],
                         {"q": "track:Song artist:Artist", "type": "track", "limit": 1})

    def test_legacy_search_returns_none_for_empty_candidate_list(self):
        session = Mock()
        spotify = Spotify(session, legacy_fallback=True)
        spotify.access_token = "test-token"
        session.request.return_value.json.return_value = {"tracks": {"items": []}}
        self.assertIsNone(spotify.search("Artist", "Song (Album Version)"))

    def test_explicit_notes_do_not_require_an_explicit_catalog_flag(self):
        session = Mock()
        spotify = Spotify(session)
        spotify.access_token = "test-token"
        session.request.return_value.json.return_value = {"tracks": {"items": [{
            "uri": "spotify:track:match", "name": "Don't Wanna Go Home",
            "artists": [{"name": "Jason Derulo"}], "explicit": False,
        }]}}
        self.assertEqual(spotify.search("Jason Derülo", "Don't Wanna Go Home (Explicit)"),
                         "spotify:track:match")

    def test_catalog_fallback_requires_the_reviewed_release_and_recording(self):
        session = Mock()
        spotify = Spotify(session)
        spotify.access_token = "test-token"
        correct = {
            "id": "1ko2lVN0vKGUl9zrU0qSlT", "uri": "spotify:track:1ko2lVN0vKGUl9zrU0qSlT",
            "external_ids": {"isrc": "USEE19901162"}, "is_playable": True,
        }
        other_edit = {**correct, "id": "another-release", "uri": "spotify:track:another-release"}

        def request(method, url, **kwargs):
            response = Mock(ok=True)
            query = kwargs["params"]["q"]
            tracks = [other_edit, correct] if query == "isrc:USEE19901162" else []
            response.json.return_value = {"tracks": {"items": tracks}}
            return response

        session.request.side_effect = request
        artist = "Bill Withers & Grover Washingt"
        with self.assertLogs(level="INFO"):
            self.assertEqual(spotify.search(artist, "Just the Two of Us"), correct["uri"])
        self.assertIsNone(spotify.search(artist, "Just the Two of Us (Acoustic)"))
        correct["is_playable"] = False
        self.assertIsNone(spotify.search(artist, "Just the Two of Us"))

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
