import unittest
from unittest.mock import Mock

from shared.spotify_matching import find_track


class SpotifyMatchingTests(unittest.TestCase):
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
                api = Mock(side_effect=[
                    {"tracks": {"items": []}},
                    {"tracks": {"items": [
                        self.candidate("wrong", catalog_title, credits[0]),
                        self.candidate("right", catalog_title, *credits),
                    ]}},
                ])
                self.assertEqual(find_track(api, artist, title, ""), "right")
                query = api.call_args.kwargs["params"]["q"]
                self.assertEqual(query, f"track:{catalog_title} artist:{credits[0]}")

    def test_search_uses_unambiguous_parenthetical_artist_credit(self):
        studio = self.candidate("studio", "If You're Over Me", "Olly Alexander (Years & Years)")
        acoustic = self.candidate(
            "acoustic", "If You're Over Me - Acoustic", "Olly Alexander (Years & Years)"
        )
        partial = self.candidate(
            "partial", "If You're Over Me - Acoustic", "Another Artist (Years & Years Tribute)"
        )
        api = Mock(return_value={"tracks": {"items": [studio, partial, acoustic]}})
        source_title = "If You're Over Me (Acoustic)"
        self.assertEqual(find_track(api, "Years & Years", source_title, ""), "acoustic")

        ambiguous = dict(acoustic, uri="another-recording")
        api.return_value = {"tracks": {"items": [acoustic, ambiguous]}}
        self.assertIsNone(find_track(api, "Years & Years", source_title, ""))

    def test_search_matches_a_different_parenthetical_artist_credit(self):
        candidate = self.candidate("match", "Evening Lights", "New Name (Former Name)")
        api = Mock(return_value={"tracks": {"items": [candidate]}})

        self.assertEqual(find_track(api, "former  NAME", "Evening Lights", ""), "match")

    def test_search_keeps_explicit_versions_including_radio_edit(self):
        for version in ("Radio Edit", "Live", "Acoustic", "Stripped", "Club Remix"):
            with self.subTest(version=version):
                base = self.candidate("base", "Song", "Artist")
                correct = self.candidate("version", f"Song - {version}", "Artist")
                remix = version if version == "Club Remix" else ""
                title = "Song" if remix else f"Song ({version})"
                api = Mock(return_value={"tracks": {"items": [base, correct]}})
                self.assertEqual(find_track(api, "Artist", title, remix), "version")
                api.return_value = {"tracks": {"items": [base]}}
                self.assertIsNone(find_track(api, "Artist", title, remix))

    def test_search_prefers_literal_match_for_band_name(self):
        api = Mock(return_value={"tracks": {"items": [
            self.candidate("split", "Song", "Earth", "Wind", "Fire"),
            self.candidate("band", "Song", "Earth, Wind & Fire"),
        ]}})
        self.assertEqual(find_track(api, "Earth, Wind & Fire", "Song", ""), "band")
        api.assert_called_once()

    def test_plain_search_fallback_requires_the_tracking_room_recording(self):
        original = self.candidate("original", "Delicate", "Taylor Swift")
        recording = self.candidate(
            "studio", "Delicate - Recorded at The Tracking Room Nashville", "Taylor Swift"
        )
        api = Mock(side_effect=[
            {"tracks": {"items": [original]}},
            {"tracks": {"items": [original, recording]}},
        ])
        title = "Delicate (Nashvilles Tracking Room Studios)"
        self.assertEqual(find_track(api, "Taylor Swift", title, ""), "studio")
        self.assertEqual(api.call_args.kwargs["params"]["q"], f"{title} Taylor Swift")

    def test_search_does_not_duplicate_version_present_in_both_fields(self):
        for artist, title, version, catalog_title, credits in (
            ("Billy Raffoul", "Homebody (feat. OSKA) [The Red Version]", "The Red Version",
             "Homebody - The Red Version", ("Billy Raffoul", "OSKA")),
            ("Betcha", "July (Reimagined)", "Reimagined", "July - Reimagined", ("Betcha",)),
        ):
            with self.subTest(title=title):
                api = Mock(return_value={"tracks": {"items": [
                    self.candidate("right", catalog_title, *credits),
                ]}})
                self.assertEqual(find_track(api, artist, title, version), "right")
                for request in api.call_args_list:
                    self.assertEqual(request.kwargs["params"]["q"].count(version), 1)

    def test_plain_search_rejects_cover_with_source_artist_only_in_title(self):
        api = Mock(return_value={"tracks": {"items": [
            self.candidate("cover", "I Can Feel It (Originally Performed by Sickick)", "Vox Freaks"),
        ]}})
        self.assertIsNone(find_track(api, "Sickick", "I Can Feel It", ""))

    def test_unique_lead_artist_recovers_missing_collaborator(self):
        api = Mock(return_value={"tracks": {"items": [
            self.candidate("cover", "Aliens Need Love Too", "Another Artist"),
            self.candidate("live", "Aliens Need Love Too - Live", "Phlake"),
            self.candidate("match", "Aliens Need Love Too", "Phlake"),
        ]}})
        self.assertEqual(find_track(api, "Phlake & Mercedes the Virus",
                                       "Aliens Need Love Too", ""), "match")

    def test_lead_artist_fallback_skips_distinct_recordings(self):
        api = Mock(side_effect=[
            {"tracks": {"items": [self.candidate("one", "Song", "Artist")]}},
            {"tracks": {"items": [self.candidate("two", "Song", "Artist")]}},
            {"tracks": {"items": []}},
        ])
        self.assertIsNone(find_track(api, "Artist & Guest", "Song", ""))

    def test_lead_artist_fallback_accepts_releases_of_same_recording(self):
        candidates = [self.candidate(uri, "Song - Radio Edit", "Artist") for uri in ("single", "album")]
        for candidate in candidates:
            candidate["external_ids"] = {"isrc": "TEST00000001"}
        api = Mock(return_value={"tracks": {"items": candidates}})
        self.assertEqual(find_track(api, "Artist & Guest", "Song (Radio Edit)", ""), "single")

    def test_recovery_handles_punctuation_verified_aliases_and_credit_formats(self):
        cases = (
            ("Trey Songz", "Missin You", "Missin' You", ("Trey Songz",)),
            ("Lauryn Hill", "Doo Wop (That Thing)", "Doo Wop (That Thing)", ("Ms. Lauryn Hill",)),
            ("Michael Brun & Roy English", "Tongue Tied July", "Tongue Tied July",
             ("Michaël Brun", "Roy English")),
            ("Olly Murs feat. Flo Rida", "Troublemaker", "Troublemaker (feat. Flo Rida)",
             ("Olly Murs", "Flo Rida")),
            ("Ruthie Foster", "Walk On (Album Version)", "Walk on", ("Ruthie Foster",)),
        )
        for artist, title, actual_title, credits in cases:
            with self.subTest(title=title):
                track = self.candidate("right", actual_title, *credits)
                api = Mock(return_value={"tracks": {"items": [track]}})
                self.assertEqual(find_track(api, artist, title, ""), "right")

    def test_recovery_query_removes_bracketed_feature_but_requires_the_guest(self):
        correct = self.candidate("right", "Song - Acoustic", "Artist", "Guest")
        studio = self.candidate("studio", "Song", "Artist", "Guest")
        uncredited = self.candidate("uncredited", "Song - Acoustic", "Artist")

        def api(method, path, *, params):
            if params["q"] == "track:Song (Acoustic) artist:Artist":
                return {"tracks": {"items": [studio, uncredited, correct]}}
            return {"tracks": {"items": []}}

        self.assertEqual(find_track(api, "Artist", "Song (Acoustic) [feat. Guest]", ""), "right")

    def test_recovery_rejects_multiple_recordings_and_wrong_versions(self):
        candidates = [
            self.candidate("first", "Missin' You", "Artist"),
            self.candidate("second", "Missin’ You", "Artist"),
        ]
        api = Mock(return_value={"tracks": {"items": candidates}})
        self.assertIsNone(find_track(api, "Artist", "Missin You", ""))
        self.assertIsNone(find_track(api, "Artist", "Missin You (Acoustic)", ""))

    def test_recovery_requires_credits_on_artist_annotations(self):
        api = Mock(return_value={"tracks": {"items": [
            self.candidate("wrong", "Song (with Someone Else)", "Artist"),
        ]}})
        self.assertIsNone(find_track(api, "Artist", "Song", ""))
