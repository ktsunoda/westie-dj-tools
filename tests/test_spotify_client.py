import unittest
from unittest.mock import Mock

from shared.spotify.client import is_invalid_grant, refresh_access_token, request_spotify


class SpotifyClientTests(unittest.TestCase):
    def test_refresh_returns_response_for_caller_to_handle(self):
        post = Mock()
        self.assertIs(refresh_access_token(post, "client", "secret", "refresh"), post.return_value)
        post.assert_called_once_with(
            "https://accounts.spotify.com/api/token",
            auth=("client", "secret"),
            data={"grant_type": "refresh_token", "refresh_token": "refresh"},
            timeout=30,
        )

    def test_only_invalid_grant_requires_reauthorization(self):
        for status, payload, expected in (
            (400, {"error": "invalid_grant"}, True),
            (400, {"error": "invalid_client"}, False),
            (429, {"error": "invalid_grant"}, False),
            (503, {"error": "invalid_grant"}, False),
        ):
            with self.subTest(status=status, payload=payload):
                response = Mock(status_code=status)
                response.json.return_value = payload
                self.assertEqual(is_invalid_grant(response), expected)
        response = Mock(status_code=400)
        response.json.side_effect = ValueError
        self.assertFalse(is_invalid_grant(response))

    def test_failed_write_is_sent_only_once(self):
        send = Mock(side_effect=TimeoutError)
        with self.assertRaises(TimeoutError):
            request_spotify(send, "POST", "/playlists/test/items", access_token="token",
                            json={"uris": ["spotify:track:test"]})
        send.assert_called_once_with(
            "POST", "https://api.spotify.com/v1/playlists/test/items",
            headers={"Authorization": "Bearer token"}, timeout=30,
            json={"uris": ["spotify:track:test"]},
        )
