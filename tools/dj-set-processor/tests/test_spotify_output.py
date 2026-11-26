import unittest
from unittest.mock import Mock, call

from spotify_output import SpotifyPlaylist
from workflow import parse_filename


class SpotifyOutputTests(unittest.TestCase):
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
