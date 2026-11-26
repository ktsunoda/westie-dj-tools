"""Create a private Spotify playlist from a VirtualDJ set."""

import logging
import os
import re

LOGGER = logging.getLogger(__name__)
API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SETTINGS = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN")
FEATURED = re.compile(r"\s*\((?:feat\.?|ft\.?|featuring)\s+([^()]+)\)", re.I)
CREDIT_SEPARATOR = re.compile(r"\s*[,;]\s*|\s+(?:&|and|x)\s+", re.I)
VERSION_SUFFIX = re.compile(
    r"\s*[\[(]([^()[\]]*\b(?:mix|remix|edit|version|live|acoustic|stripped|reimagined)\b"
    r"[^()[\]]*)[\])]$",
    re.I,
)
TRACKING_ROOM = re.compile(
    r"(?:\s*\(nashville['’]?s tracking room studios\)|"
    r"\s+-\s+recorded at the tracking room nashville)$", re.I
)


class SpotifyReauthorizationRequired(Exception):
    """Spotify rejected the configured refresh token with invalid_grant."""


def normalized(value):
    return " ".join(value.casefold().split())


def title_key(title):
    # Spotify uses both parentheses and a dash around the same version label.
    title = FEATURED.sub("", title)
    title = VERSION_SUFFIX.sub(r" - \1", title)
    return normalized(TRACKING_ROOM.sub(" - tracking room nashville", title))


def credits_match(credit, artists):
    if normalized(credit) in artists:
        return True
    names = [normalized(name) for name in CREDIT_SEPARATOR.split(credit)]
    return len(names) > 1 and all(name in artists for name in names)


class SpotifyPlaylist:
    def __init__(self, session):
        self.session = session
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
        # VirtualDJ can put the same version in both title and remix fields.
        if remix:
            base = re.sub(
                rf"(?:\s*\({re.escape(remix)}\)|\s*\[{re.escape(remix)}\]|"
                rf"\s+-\s+{re.escape(remix)})$", "", title, flags=re.I
            ).rstrip()
            names = tuple(dict.fromkeys((f"{base} ({remix})", f"{base} - {remix}")))
            if base != title:
                names = (title, *[name for name in names if name != title])
        else:
            names = (title,)
        exact_titles = {normalized(name) for name in names}
        title_keys = {title_key(name) for name in names}
        featured = FEATURED.findall(title)
        queries = [f"track:{name} artist:{artist}" for name in names]
        exact_query_count = len(queries)
        lead = CREDIT_SEPARATOR.split(artist, maxsplit=1)[0]
        for name in names:
            clean = FEATURED.sub("", name)
            queries.extend((f"track:{clean} artist:{artist}", f"track:{clean} artist:{lead}"))
        queries.extend(f"{FEATURED.sub('', name)} {artist}" for name in names)

        fallback = None
        alias_matches = {}
        lead_matches = {}
        source_credit = normalized(artist)
        allow_lead_match = lead != artist or bool(featured)
        lead_credit = normalized(lead)
        for index, query in enumerate(dict.fromkeys(queries)):
            results = self.api(
                "GET", "/search", access_token,
                params={"q": query, "type": "track", "limit": 10},
            )["tracks"]["items"]
            for item in results:
                artists = {normalized(entry["name"]) for entry in item["artists"]}
                if normalized(item["name"]) in exact_titles and normalized(artist) in artists:
                    return item["uri"]
                if title_key(item["name"]) not in title_keys:
                    continue
                if (fallback is None and credits_match(artist, artists)
                        and all(credits_match(name, artists) for name in featured)):
                    fallback = item["uri"]
                uri = item["uri"]
                recording = (item.get("external_ids", {}).get("isrc")
                             or item.get("linked_from", {}).get("uri") or uri)
                aliases = {alias.strip() for name in artists
                           for alias in re.findall(r"\(([^()]+)\)$", name)}
                if source_credit in aliases and all(credits_match(name, artists) for name in featured):
                    alias_matches.setdefault(recording, uri)
                if allow_lead_match and lead_credit in artists:
                    lead_matches.setdefault(recording, uri)
            if fallback is not None and index >= exact_query_count - 1:
                return fallback
        if alias_matches:
            return next(iter(alias_matches.values())) if len(alias_matches) == 1 else None
        if len(lead_matches) == 1:
            return next(iter(lead_matches.values()))
        return None

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
