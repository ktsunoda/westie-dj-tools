import unittest
from unittest.mock import Mock

from shared.spotify import find_track


class SpotifyMatchingTests(unittest.TestCase):
    @staticmethod
    def candidate(uri, title, *artists):
        return {"uri": uri, "name": title, "artists": [{"name": name} for name in artists]}

    def test_album_parenthetical_ranks_matching_recordings(self):
        artist = "Eva Cassidy"
        items = []
        for album, duration in (("Eva By Heart", 244093), ("Songbird", 242040),
                                ("Best Of", 242146), ("Simply Eva", 160040)):
            track = self.candidate(album, f"Wade In The Water ({album})", artist)
            items.append(dict(track, album={"name": album}, duration_ms=duration))
        api = Mock(return_value={"tracks": {"items": items}})

        self.assertEqual(find_track(api, artist, "Wade In The Water", ""), "Eva By Heart")
        self.assertEqual(find_track(api, artist, "Wade In The Water", "", duration_ms=120000), "Eva By Heart")
        self.assertEqual(find_track(api, artist, "Wade In The Water", "", duration_ms=242000), "Songbird")
        self.assertEqual(find_track(api, artist, "Wade In The Water", "", duration_ms=160000), "Simply Eva")

    def test_subtitle_retry_accepts_shortened_edits_and_preserves_versions(self):
        studio = dict(self.candidate("studio", "Breakeven", "The Script"), duration_ms=261000)
        live = dict(self.candidate("live", "Breakeven (Live)", "The Script"), duration_ms=261000)

        def api(method, path, *, params):
            return {"tracks": {"items": [studio, live]
                               if params["q"] == "track:Breakeven artist:The Script" else []}}

        self.assertEqual(find_track(api, "The Script", "Breakeven (Falling to Pieces)", "",
                                    duration_ms=261000), "studio")
        self.assertEqual(find_track(api, "The Script", "Breakeven (Falling to Pieces)", "",
                                    duration_ms=120000), "studio")
        self.assertEqual(find_track(api, "The Script", "Breakeven (Falling to Pieces)", ""), "studio")
        self.assertIsNone(find_track(api, "The Script", "Breakeven (Radio Edit)", "",
                                     duration_ms=261000))

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

    def test_search_ranks_matches_with_parenthetical_artist_credit(self):
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
        self.assertEqual(find_track(api, "Years & Years", source_title, ""), "acoustic")

    def test_search_matches_a_different_parenthetical_artist_credit(self):
        candidate = self.candidate("match", "Evening Lights", "New Name (Former Name)")
        api = Mock(return_value={"tracks": {"items": [candidate]}})

        self.assertEqual(find_track(api, "former  NAME", "Evening Lights", ""), "match")

    def test_search_preserves_a_matching_version_and_requires_named_variants(self):
        for version in ("Radio Edit", "Live", "Acoustic", "Stripped", "Club Remix"):
            with self.subTest(version=version):
                base = self.candidate("base", "Song", "Artist")
                correct = self.candidate("version", f"Song - {version}", "Artist")
                remix = version if version == "Club Remix" else ""
                title = "Song" if remix else f"Song ({version})"
                api = Mock(return_value={"tracks": {"items": [base, correct]}})
                self.assertEqual(find_track(api, "Artist", title, remix), "version")
                api.return_value = {"tracks": {"items": [base]}}
                self.assertEqual(find_track(api, "Artist", title, remix),
                                 "base" if version == "Radio Edit" else None)

    def test_search_prefers_literal_match_for_band_name(self):
        api = Mock(return_value={"tracks": {"items": [
            self.candidate("split", "Song", "Earth", "Wind", "Fire"),
            self.candidate("band", "Song", "Earth, Wind & Fire"),
        ]}})
        self.assertEqual(find_track(api, "Earth, Wind & Fire", "Song", ""), "band")
        api.assert_called_once()

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

    def test_lead_artist_fallback_uses_search_order_when_duration_is_far_off(self):
        api = Mock(side_effect=[
            {"tracks": {"items": [dict(self.candidate("one", "Song", "Artist"), duration_ms=240000)]}},
            {"tracks": {"items": [dict(self.candidate("two", "Song", "Artist"), duration_ms=180000)]}},
            {"tracks": {"items": []}},
        ])
        self.assertEqual(find_track(api, "Artist & Guest", "Song", "", duration_ms=120000), "one")

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

    def test_recovery_prefers_search_order_and_rejects_wrong_versions(self):
        candidates = [
            self.candidate("first", "Missin' You", "Artist"),
            self.candidate("second", "Missin’ You", "Artist"),
        ]
        api = Mock(return_value={"tracks": {"items": candidates}})
        self.assertEqual(find_track(api, "Artist", "Missin You", ""), "first")
        self.assertIsNone(find_track(api, "Artist", "Missin You (Acoustic)", ""))

    def test_recovery_requires_credits_on_artist_annotations(self):
        api = Mock(return_value={"tracks": {"items": [
            self.candidate("wrong", "Song (with Someone Else)", "Artist"),
        ]}})
        self.assertIsNone(find_track(api, "Artist", "Song", ""))

    def test_recovery_handles_soundtrack_annotations_and_artist_spelling(self):
        cases = (
            ("Justin Timberlake", 'Song (Original Song From DreamWorks Animation\'s "Trolls")',
             'Song (from DreamWorks Animation\'s "TROLLS")', ("Justin Timberlake",)),
            ("Keb'Mo", "She Just Wants to Dance", "She Just Wants To Dance", ("Keb' Mo'",)),
            ("Clean Bandit", "Rather Be feat. Jess Glynne", "Rather Be (feat. Jess Glynne)",
             ("Clean Bandit", "Jess Glynne")),
            ("David Guetta", "Little Bad Girl (feat.Taio Cruz & Ludacris)",
             "Little Bad Girl (feat. Taio Cruz & Ludacris)", ("David Guetta", "Taio Cruz", "Ludacris")),
        )
        for artist, title, actual_title, credits in cases:
            with self.subTest(title=title):
                api = Mock(return_value={"tracks": {"items": [
                    self.candidate("right", actual_title, *credits),
                ]}})
                self.assertEqual(find_track(api, artist, title, ""), "right")

    def test_recovery_does_not_collapse_explicit_version_differences(self):
        track = self.candidate("wrong", "Song - Club Remix", "Artist")
        api = Mock(return_value={"tracks": {"items": [track]}})
        self.assertIsNone(find_track(api, "Artist", "Song", ""))
        self.assertIsNone(find_track(api, "Artist", "Song (Acoustic)", ""))

    def test_extended_recovery_preserves_existing_format_match(self):
        broad = self.candidate("different-release", "How Long", "Tove Lo")
        prior = self.candidate("existing-release", 'How Long - From"Euphoria" An HBO Original Series', "Tove Lo")
        api = Mock(return_value={"tracks": {"items": [broad, prior]}})
        self.assertEqual(
            find_track(api, "Tove Lo", "How Long (From ”Euphoria” An HBO Original Series)", ""),
            "existing-release",
        )

    def test_recovery_treats_different_releases_of_same_recording_as_one_match(self):
        releases = [
            self.candidate("compilation", "Finesse (feat. Cardi B) - Remix", "Bruno Mars", "Cardi B"),
            self.candidate("single", "Finesse - Remix; feat. Cardi B", "Bruno Mars", "Cardi B"),
        ]
        for track in releases:
            track["external_ids"] = {"isrc": "USAT21705441"}
        api = Mock(return_value={"tracks": {"items": releases}})

        self.assertIn(
            find_track(api, "Bruno Mars", "Finesse (Remix) [feat. Cardi B]", ""),
            {"compilation", "single"},
        )

    def test_recovery_compares_spelled_out_numbers_and_catalog_credits(self):
        correct = self.candidate("right", "1, 2 Step (feat. Missy Elliott)", "Ciara", "Missy Elliott")
        wrong = self.candidate("wrong", "1, 2 Step - Remix", "Ciara", "Missy Elliott")

        def api(method, path, *, params):
            items = [wrong, correct] if params["q"] == "1 2 step Ciara" else []
            return {"tracks": {"items": items}}

        self.assertEqual(find_track(api, "Ciara", "One, Two Step (feat. Missy Elliott)", ""), "right")
        self.assertIsNone(find_track(api, "Ciara", "One, Two Step (feat. Someone Else)", ""))

    def test_recovery_uses_cover_performer_and_keeps_requested_version(self):
        correct = self.candidate("right", "Someone You Loved", "Cover Performer")
        original = self.candidate("original", "Someone You Loved", "Original Performer")

        def api(method, path, *, params):
            items = [original, correct] if params["q"] == "Someone You Loved Cover Performer" else []
            return {"tracks": {"items": items}}

        self.assertEqual(
            find_track(api, "Cover Performer", "Someone You Loved (Original Performer Cover)", ""),
            "right",
        )
        self.assertIsNone(
            find_track(api, "Cover Performer", "Someone You Loved (Original Performer Acoustic Cover)", "")
        )

    def test_recovery_matches_live_label_only_for_same_venue(self):
        correct = self.candidate("right", "Attention - Live from Spotify Studios NYC", "Charlie Puth")
        wrong = self.candidate("wrong", "Attention - Live from Spotify Studios London", "Charlie Puth")
        api = Mock(return_value={"tracks": {"items": [wrong, correct]}})
        self.assertEqual(
            find_track(api, "Charlie Puth", "Attention - Recorded at Spotify Studios NYC", ""),
            "right",
        )
        self.assertIsNone(
            find_track(api, "Charlie Puth", "Attention - Recorded at Spotify Studios LA", "")
        )

    def test_credit_query_preserves_the_requested_version(self):
        correct = self.candidate("right", "Only Here for a Little While - Acoustic", "Artist", "Guest")
        studio = self.candidate("studio", "Only Here for a Little While", "Artist", "Guest")

        def api(method, path, *, params):
            items = [studio, correct] if params["q"] == "Artist Guest Only Here for a Little While" else []
            return {"tracks": {"items": items}}

        self.assertEqual(
            find_track(api, "Artist", "Only Here for a Little While (Acoustic) (feat. Guest)", ""), "right"
        )

    def test_catalog_alias_requires_the_verified_artist_id(self):
        track = self.candidate("right", "Sandman", "Gaustad")
        api = Mock(return_value={"tracks": {"items": [track]}})
        self.assertIsNone(find_track(api, "Grace Gaustad", "Sandman", ""))
        track["artists"][0]["id"] = "5MZxzpsn1zTTkiYwQ9Qz9b"
        self.assertEqual(find_track(api, "Grace Gaustad", "Sandman", ""), "right")

    def test_duet_labels_require_both_performers(self):
        duet = self.candidate("right", "Hold My Hand (with Akon)", "Michael Jackson", "Akon")
        solo = self.candidate("solo", "Hold My Hand", "Michael Jackson")
        api = Mock(return_value={"tracks": {"items": [solo, duet]}})
        self.assertEqual(
            find_track(api, "Michael Jackson & Akon", "Hold My Hand (Duet with Akon)", ""),
            "right",
        )

    def test_original_cover_credits_are_not_required_on_the_cover_recording(self):
        cover = self.candidate("cover", "La La La", "Noah Guthrie")
        original = self.candidate("original", "La La La", "Naughty Boy", "Sam Smith")
        guest = self.candidate("guest", "La La La (feat. Noah Guthrie)", "Other Artist", "Noah Guthrie")
        api = Mock(return_value={"tracks": {"items": [original, guest, cover]}})
        self.assertEqual(
            find_track(api, "Noah Guthrie", "La La La (Naughty Boy ft. Sam Smith Cover)", ""),
            "cover",
        )
        self.assertIsNone(
            find_track(api, "Noah Guthrie", "La La La (Naughty Boy ft. Sam Smith Clean Cover)", "")
        )

    def test_prefers_clean_among_equivalent_search_results(self):
        explicit = self.candidate("explicit", "1, 2 Step", "Ciara")
        clean = self.candidate("clean", "1, 2 Step", "Ciara")
        explicit.update(explicit=True, duration_ms=200000)
        clean.update(explicit=False, duration_ms=200000)
        api = Mock(return_value={"tracks": {"items": [explicit, clean]}})
        self.assertEqual(find_track(api, "Ciara", "1, 2 Step", ""), "clean")
        self.assertEqual(find_track(api, "Ciara", "One, Two Step", ""), "clean")
        clean["duration_ms"] = 180000
        self.assertEqual(find_track(api, "Ciara", "One, Two Step", ""), "explicit")
        self.assertEqual(find_track(api, "Ciara", "1, 2 Step", ""), "explicit")

    def test_recovery_uses_catalog_explicit_flag_for_requested_version(self):
        explicit = self.candidate("explicit", "Don't Wanna Go Home", "Jason Derulo")
        clean = self.candidate("clean", "Don't Wanna Go Home", "Jason Derulo")
        explicit["explicit"] = True
        clean["explicit"] = False
        api = Mock(return_value={"tracks": {"items": [explicit, clean]}})
        self.assertEqual(find_track(api, "Jason Derülo", "Don't Wanna Go Home (Clean)", ""), "clean")
        self.assertEqual(find_track(api, "Jason Derülo", "Don't Wanna Go Home (Explicit)", ""), "explicit")
        self.assertEqual(find_track(api, "Jason Derülo", "Don't Wanna Go Home (Clean Edit)", ""), "clean")
        api.return_value = {"tracks": {"items": [explicit]}}
        self.assertIsNone(find_track(api, "Jason Derülo", "Don't Wanna Go Home (Clean Edit)", ""))

    def test_acoustic_album_can_supply_missing_track_version(self):
        studio = self.candidate("studio", "Song", "Artist")
        acoustic = self.candidate("acoustic", "Song", "Artist")
        acoustic["album"] = {"name": "Album Acoustic"}
        api = Mock(return_value={"tracks": {"items": [studio, acoustic]}})
        self.assertEqual(find_track(api, "Artist", "Song (Acoustic Version)", ""), "acoustic")
        self.assertEqual(find_track(api, "Artist", "Song (Acoustic)", ""), "acoustic")
        acoustic["name"] = "Song - Live"
        self.assertIsNone(find_track(api, "Artist", "Song (Acoustic Version)", ""))

    def test_contest_partial_edit_fallback_preserves_requested_remix(self):
        studio = self.candidate("studio", "Song", "Artist")
        remix = self.candidate("remix", "Song - Named Remix", "Artist")
        api = Mock(return_value={"tracks": {"items": [studio, remix]}})
        self.assertEqual(find_track(api, "Artist", "Song (Named Remix) (Contest Partial Edit)", ""), "remix")
        self.assertIsNone(find_track(api, "Artist", "Song (Extended)", ""))
        api.return_value = {"tracks": {"items": [studio]}}
        self.assertEqual(find_track(api, "Artist", "Song (Contest Partial Edit)", ""), "studio")
        self.assertIsNone(find_track(api, "Artist", "Song (Named Remix) (Contest Partial Edit)", ""))

    def test_verified_artist_rename_also_applies_to_guest_credits(self):
        track = self.candidate("track", "Song (feat. Grace Gaustad)", "Artist", "Gaustad")
        api = Mock(return_value={"tracks": {"items": [track]}})
        self.assertIsNone(find_track(api, "Artist", "Song (feat. Grace Gaustad)", ""))
        track["artists"][1]["id"] = "5MZxzpsn1zTTkiYwQ9Qz9b"
        self.assertEqual(find_track(api, "Artist", "Song (feat. Grace Gaustad)", ""), "track")

    def test_recovery_equates_release_metadata_without_dropping_a_named_edit(self):
        source = self.candidate("right", "Soul Serenade - Remastered 2002", "The Jimmy Castor Bunch")
        api = Mock(return_value={"tracks": {"items": [source]}})
        self.assertEqual(find_track(api, "The Jimmy Castor Bunch", "Soul Serenade (Remastered)", ""), "right")
        self.assertIsNone(find_track(api, "The Jimmy Castor Bunch", "Soul Serenade (2020 Remaster)", ""))
        self.assertIsNone(find_track(api, "The Jimmy Castor Bunch", "Soul Serenade (Live)", ""))

    def test_duration_breaks_ties_between_otherwise_matching_recordings(self):
        first = self.candidate("first", "Walk On", "Ruthie Foster")
        second = self.candidate("second", "Walk On", "Ruthie Foster")
        first["duration_ms"] = 276000
        second["duration_ms"] = 325000
        api = Mock(return_value={"tracks": {"items": [first, second]}})
        args = (api, "Ruthie Foster", "Walk On (Album Version)", "")
        self.assertEqual(find_track(*args), "first")
        self.assertEqual(find_track(*args, duration_ms=277000), "first")
        self.assertEqual(find_track(*args, duration_ms=310000), "first")
        second["duration_ms"] = 278000
        self.assertEqual(find_track(*args, duration_ms=277000), "first")
        self.assertEqual(find_track(api, "Ruthie Foster", "Walk On", "", duration_ms=278000), "second")

    def test_recovery_does_not_reject_a_unique_match_by_duration(self):
        track = self.candidate("original", "Don't Wanna Go Home", "Jason Derulo")
        track.update(explicit=False, duration_ms=206080)
        api = Mock(return_value={"tracks": {"items": [track]}})
        self.assertEqual(find_track(api, "Jason Derülo", "Don't Wanna Go Home (Clean)", "",
                                    duration_ms=180000), "original")

    def test_versus_label_requires_both_artists_to_be_credited(self):
        correct = self.candidate("right", "This Girl (Kungs Vs. Cookin' On 3 Burners)",
                                 "Kungs", "Cookin' On 3 Burners")
        wrong = self.candidate("wrong", correct["name"], "Kungs")
        api = Mock(return_value={"tracks": {"items": [wrong, correct]}})
        self.assertEqual(find_track(api, "Kungs & Cookin' On 3 Burners", "This Girl", ""), "right")

    def test_radio_edit_can_find_original_without_matching_duration(self):
        studio = self.candidate("studio", "Chunky", "Bruno Mars")
        studio["duration_ms"] = 186973
        api = Mock(return_value={"tracks": {"items": [studio]}})
        args = (api, "Bruno Mars", "Chunky (Radio Edit)", "")
        self.assertEqual(find_track(*args, duration_ms=187000), "studio")
        self.assertEqual(find_track(*args, duration_ms=190000), "studio")
        self.assertEqual(find_track(*args), "studio")
        studio["name"] = "Chunky - Extended Mix"
        self.assertIsNone(find_track(*args, duration_ms=187000))

    def test_clean_counterpart_can_have_small_encoding_difference_on_same_album(self):
        explicit = self.candidate("explicit", "Up Down", "T-Pain")
        clean = self.candidate("clean", "Up Down", "T-Pain")
        explicit.update(explicit=True, duration_ms=231093, album={"name": "Happy Hour"})
        clean.update(explicit=False, duration_ms=230986, album={"name": "Happy Hour"})
        api = Mock(return_value={"tracks": {"items": [explicit, clean]}})
        self.assertEqual(find_track(api, "T-Pain", "Up Down", ""), "clean")
        clean["album"]["name"] = "Other Release"
        self.assertEqual(find_track(api, "T-Pain", "Up Down", ""), "explicit")

    def test_short_edit_can_find_the_full_length_beyonce_track(self):
        original = self.candidate("original", "Partition", "Beyoncé")
        original["duration_ms"] = 319000

        def api(method, path, *, params):
            items = [original] if params["q"] == "Beyoncé Partition" else []
            return {"tracks": {"items": items}}

        self.assertEqual(find_track(api, "Beyoncé", "Partition (Short Edit)", "", duration_ms=175000),
                         "original")

    def test_search_ignores_an_unplayable_exact_match(self):
        unavailable = self.candidate("unavailable", "Song", "Artist")
        unavailable["is_playable"] = False
        playable = self.candidate("playable", "Song", "Artist")
        api = Mock(return_value={"tracks": {"items": [unavailable, playable]}})
        self.assertEqual(find_track(api, "Artist", "Song", ""), "playable")

    def test_early_matches_require_requested_guests(self):
        solo = self.candidate("solo", "Song", "Artist")
        mislabeled = self.candidate("mislabeled", "Song (feat. Guest)", "Artist")
        duet = self.candidate("duet", "Song (feat. Guest)", "Artist", "Guest")
        api = Mock(return_value={"tracks": {"items": [solo, mislabeled, duet]}})
        self.assertEqual(find_track(api, "Artist", "Song (feat. Guest)", ""), "duet")
        api.return_value = {"tracks": {"items": [solo, mislabeled]}}
        self.assertIsNone(find_track(api, "Artist", "Song (feat. Guest)", ""))

    def test_literal_clean_title_cannot_accept_an_explicit_recording(self):
        wrong = self.candidate("wrong", "Song (Clean)", "Artist")
        wrong["explicit"] = True
        api = Mock(return_value={"tracks": {"items": [wrong]}})
        self.assertIsNone(find_track(api, "Artist", "Song (Clean)", ""))
