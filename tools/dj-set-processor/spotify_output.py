"""Create a private Spotify playlist from a VirtualDJ set."""

import logging
import os
from functools import partial

from spotify_search import find_track

LOGGER = logging.getLogger(__name__)
API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SETTINGS = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN")


class SpotifyReauthorizationRequired(Exception):
    """Spotify rejected the configured refresh token with invalid_grant."""


class SpotifyPlaylist:
    def __init__(self, session, overrides=None):
        self.session = session
        self.overrides = overrides
        self.refresh_token = None

    def access_token(self):
        settings = [os.environ.get(name) for name in SETTINGS]
        if not all(settings):
            raise ValueError("Spotify credentials are incomplete")
        client_id, client_secret, configured_refresh_token = settings
        response = self.session.post(
            TOKEN_URL, auth=(client_id, client_secret),
            data={"grant_type": "refresh_token",
                  "refresh_token": self.refresh_token or configured_refresh_token},
            timeout=30,
        )
        if response.status_code != 200:
            if response.status_code == 400:
                try:
                    error = response.json()
                except ValueError:
                    error = None
                if isinstance(error, dict) and error.get("error") == "invalid_grant":
                    raise SpotifyReauthorizationRequired
            LOGGER.error("Spotify token refresh failed (HTTP %s)", response.status_code)
            response.raise_for_status()
        tokens = response.json()
        self.refresh_token = tokens.get("refresh_token") or self.refresh_token
        return tokens["access_token"]

    def api(self, method, path, access_token, **kwargs):
        response = self.session.request(
            method, API + path, headers={"Authorization": f"Bearer {access_token}"},
            timeout=30, **kwargs,
        )
        if not response.ok:
            LOGGER.error("Spotify %s failed (HTTP %s)", method, response.status_code)
        response.raise_for_status()
        return response.json()

    def search(self, access_token, artist, title, remix):
        api = partial(self.api, access_token=access_token)
        uri = find_track(api, artist, title, remix)
        if uri is None and self.overrides is not None:
            return self.overrides.resolve_miss(artist, title)
        return uri

    def __call__(self, playlist, tracks):
        token = self.access_token()
        cache = {}
        uris = []
        for track in tracks:
            artist, title, remix = (
                track.get(field, "").strip() for field in ("artist", "title", "remix")
            )
            if not artist or not title:
                continue
            key = (artist, title, remix)
            if key not in cache:
                cache[key] = self.search(token, *key)
            if cache[key] is not None:
                uris.append(cache[key])
        unmatched = len(tracks) - len(uris)
        if not uris:
            LOGGER.error("Spotify found no matching tracks (%s unmatched)", unmatched)
            raise ValueError("Spotify found no matching tracks")
        result = self.api(
            "POST", "/me/playlists", token,
            json={"name": playlist.spotify_name, "public": False, "description": ""},
        )
        path = f"/playlists/{result['id']}/items"
        for start in range(0, len(uris), 100):
            self.api("POST", path, token, json={"uris": uris[start:start + 100]})["snapshot_id"]
        LOGGER.info("Spotify playlist created (%s added, %s unmatched)", len(uris), unmatched)
