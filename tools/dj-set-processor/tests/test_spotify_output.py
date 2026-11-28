import unittest
from unittest.mock import Mock, call

from spotify_output import SpotifyPlaylist
from virtualdj import parse_m3u
from workflow import parse_filename


class SpotifyOutputTests(unittest.TestCase):
    def test_names_playlist_from_first_m3u_track_even_if_it_is_unmatched(self):
        playlist = parse_filename("id", "2026.09.01 01 Reverie Ballroom [SP].m3u")
        for time_field, suffix in (("<time>21:08</time>", " (21:08)"), ("", "")):
            with self.subTest(time_field=time_field):
                tracks = parse_m3u(
                    f"#EXTVDJ:{time_field}<lastplaytime>1788322081</lastplaytime>\nfirst.mp3\n"
                    "#EXTVDJ:<time>21:11</time><artist>Artist</artist><title>Track</title>\nsecond.mp3\n"
                )
                output = SpotifyPlaylist(Mock())
                output.access_token = Mock(return_value="access")
                output.search = Mock(return_value="spotify:track:matched")
                output.api = Mock(side_effect=[{"id": "test"}, {"snapshot_id": "one"}])

                output(playlist, tracks)

                self.assertEqual(
                    output.api.call_args_list[0].kwargs["json"]["name"],
                    f"2026.09.01 Reverie Ballroom{suffix}",
                )

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
