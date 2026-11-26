import unittest
from unittest.mock import Mock, call

from spotify_output import SpotifyPlaylist
from workflow import parse_filename


class SpotifyOutputTests(unittest.TestCase):
    @staticmethod
    def candidate(uri, title, *artists):
        return {"uri": uri, "name": title, "artists": [{"name": name} for name in artists]}

    def test_search_recovers_reported_credit_differences(self):
        cases = [
            ("Feder & UPSAHL", "Let There Be Drums", "Let There Be Drums", ("Feder", "UPSAHL")),
            ("Rihanna", "Hate That I Love You (feat. Ne-Yo)", "Hate That I Love You", ("Rihanna", "Ne-Yo")),
            ("The Natural Synthetic, Miette Hope & Ariza", "Sacrifices", "Sacrifices",
             ("The Natural Synthetic", "Miette Hope", "Ariza")),
            ("Connor Price & Nic D", "Too Easy", "Too Easy", ("Connor Price", "Nic D")),
        ]
        for artist, title, catalog_title, credits in cases:
            with self.subTest(title=title):
                output = SpotifyPlaylist(Mock())
                output.api = Mock(side_effect=[
                    {"tracks": {"items": []}},
                    {"tracks": {"items": [
                        self.candidate("wrong", catalog_title, credits[0]),
                        self.candidate("right", catalog_title, *credits),
                    ]}},
                ])
                self.assertEqual(output.search("access", artist, title, ""), "right")
                query = output.api.call_args.kwargs["params"]["q"]
                self.assertEqual(query, f"track:{catalog_title} artist:{credits[0]}")

    def test_search_uses_unambiguous_parenthetical_artist_credit(self):
        output = SpotifyPlaylist(Mock())
        studio = self.candidate("studio", "If You're Over Me", "Olly Alexander (Years & Years)")
        acoustic = self.candidate(
            "acoustic", "If You're Over Me - Acoustic", "Olly Alexander (Years & Years)"
        )
        partial = self.candidate(
            "partial", "If You're Over Me - Acoustic", "Another Artist (Years & Years Tribute)"
        )
        output.api = Mock(return_value={"tracks": {"items": [studio, partial, acoustic]}})
        source_title = "If You're Over Me (Acoustic)"
        self.assertEqual(output.search("access", "Years & Years", source_title, ""), "acoustic")

        ambiguous = dict(acoustic, uri="another-recording")
        output.api.return_value = {"tracks": {"items": [acoustic, ambiguous]}}
        self.assertIsNone(output.search("access", "Years & Years", source_title, ""))

    def test_search_matches_a_different_parenthetical_artist_credit(self):
        output = SpotifyPlaylist(Mock())
        candidate = self.candidate("match", "Evening Lights", "New Name (Former Name)")
        output.api = Mock(return_value={"tracks": {"items": [candidate]}})

        self.assertEqual(output.search("access", "former  NAME", "Evening Lights", ""), "match")

    def test_search_keeps_explicit_versions_including_radio_edit(self):
        for version in ("Radio Edit", "Live", "Acoustic", "Stripped", "Club Remix"):
            with self.subTest(version=version):
                output = SpotifyPlaylist(Mock())
                base = self.candidate("base", "Song", "Artist")
                correct = self.candidate("version", f"Song - {version}", "Artist")
                remix = version if version == "Club Remix" else ""
                title = "Song" if remix else f"Song ({version})"
                output.api = Mock(return_value={"tracks": {"items": [base, correct]}})
                self.assertEqual(output.search("access", "Artist", title, remix), "version")
                output.api.return_value = {"tracks": {"items": [base]}}
                self.assertIsNone(output.search("access", "Artist", title, remix))

    def test_search_prefers_literal_match_for_band_name(self):
        output = SpotifyPlaylist(Mock())
        output.api = Mock(return_value={"tracks": {"items": [
            self.candidate("split", "Song", "Earth", "Wind", "Fire"),
            self.candidate("band", "Song", "Earth, Wind & Fire"),
        ]}})
        self.assertEqual(output.search("access", "Earth, Wind & Fire", "Song", ""), "band")
        output.api.assert_called_once()

    def test_plain_search_fallback_requires_the_tracking_room_recording(self):
        output = SpotifyPlaylist(Mock())
        original = self.candidate("original", "Delicate", "Taylor Swift")
        recording = self.candidate(
            "studio", "Delicate - Recorded at The Tracking Room Nashville", "Taylor Swift"
        )
        output.api = Mock(side_effect=[
            {"tracks": {"items": [original]}},
            {"tracks": {"items": [original, recording]}},
        ])
        title = "Delicate (Nashvilles Tracking Room Studios)"
        self.assertEqual(output.search("access", "Taylor Swift", title, ""), "studio")
        self.assertEqual(output.api.call_args.kwargs["params"]["q"], f"{title} Taylor Swift")

    def test_search_does_not_duplicate_version_present_in_both_fields(self):
        for artist, title, version, catalog_title, credits in (
            ("Billy Raffoul", "Homebody (feat. OSKA) [The Red Version]", "The Red Version",
             "Homebody - The Red Version", ("Billy Raffoul", "OSKA")),
            ("Betcha", "July (Reimagined)", "Reimagined", "July - Reimagined", ("Betcha",)),
        ):
            with self.subTest(title=title):
                output = SpotifyPlaylist(Mock())
                output.api = Mock(return_value={"tracks": {"items": [
                    self.candidate("right", catalog_title, *credits),
                ]}})
                self.assertEqual(output.search("access", artist, title, version), "right")
                for request in output.api.call_args_list:
                    self.assertEqual(request.kwargs["params"]["q"].count(version), 1)

    def test_plain_search_rejects_cover_with_source_artist_only_in_title(self):
        output = SpotifyPlaylist(Mock())
        output.api = Mock(return_value={"tracks": {"items": [
            self.candidate("cover", "I Can Feel It (Originally Performed by Sickick)", "Vox Freaks"),
        ]}})
        self.assertIsNone(output.search("access", "Sickick", "I Can Feel It", ""))

    def test_unique_lead_artist_recovers_missing_collaborator(self):
        output = SpotifyPlaylist(Mock())
        output.api = Mock(return_value={"tracks": {"items": [
            self.candidate("cover", "Aliens Need Love Too", "Another Artist"),
            self.candidate("live", "Aliens Need Love Too - Live", "Phlake"),
            self.candidate("match", "Aliens Need Love Too", "Phlake"),
        ]}})
        self.assertEqual(output.search("access", "Phlake & Mercedes the Virus",
                                       "Aliens Need Love Too", ""), "match")

    def test_lead_artist_fallback_skips_distinct_recordings(self):
        output = SpotifyPlaylist(Mock())
        output.api = Mock(side_effect=[
            {"tracks": {"items": [self.candidate("one", "Song", "Artist")]}},
            {"tracks": {"items": [self.candidate("two", "Song", "Artist")]}},
            {"tracks": {"items": []}},
        ])
        self.assertIsNone(output.search("access", "Artist & Guest", "Song", ""))

    def test_lead_artist_fallback_accepts_releases_of_same_recording(self):
        output = SpotifyPlaylist(Mock())
        candidates = [self.candidate(uri, "Song - Radio Edit", "Artist") for uri in ("single", "album")]
        for candidate in candidates:
            candidate["external_ids"] = {"isrc": "TEST00000001"}
        output.api = Mock(return_value={"tracks": {"items": candidates}})
        self.assertEqual(output.search("access", "Artist & Guest", "Song (Radio Edit)", ""), "single")

    def test_creates_private_playlist_and_preserves_repeats_across_batches(self):
        output = SpotifyPlaylist(Mock())
        output.access_token = Mock(return_value="access")
        output.search = Mock(side_effect=["spotify:track:matched", None])
        output.api = Mock(side_effect=[{"id": "test"}, {"snapshot_id": "one"}, {"snapshot_id": "two"}])
        playlist = parse_filename("id", "2026.10.10 01 Event [DH ✅; SP].m3u")
        matched = {"artist": "Artist", "title": "Track"}
        unmatched = {"artist": "Artist", "title": "Missing"}
        output(playlist, [matched] * 100 + [unmatched, matched])
        self.assertEqual(output.api.call_args_list, [
            call("POST", "/me/playlists", "access",
                 json={"name": "2026.10.10 Event", "public": False, "description": ""}),
            call("POST", "/playlists/test/items", "access", json={"uris": ["spotify:track:matched"] * 100}),
            call("POST", "/playlists/test/items", "access", json={"uris": ["spotify:track:matched"]}),
        ])
        self.assertEqual(output.search.call_count, 2)

    def test_no_matches_does_not_create_empty_playlist(self):
        output = SpotifyPlaylist(Mock())
        output.access_token = Mock(return_value="access")
        output.search = Mock(return_value=None)
        output.api = Mock()
        with self.assertRaises(ValueError):
            output(Mock(), [{"artist": "Artist", "title": "Track"}])
        output.api.assert_not_called()
