"""Build Spotify contest playlists by division from DJ History."""
import argparse
import json
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote

from shared.spotify_matching import find_track

DIVISIONS = {
    "Newcomer": ("newcomer",),
    "Novice": ("novice",),
    "Intermediate": ("intermediate",),
    "Advanced": ("advanced",),
    "All Star": ("all-star", "all star", "all-stars"),
    "Champions": ("champion", "champions", "champ"),
}
STATE = "'Spotify Sync State'"
FAILED = "'Failed Searches'"
LEGACY_TRACK_SUFFIXES = (
    r"\[feat\.[^\]]*\]",
    r"\(feat\.[^)]*\)",
    r"\(radio edit\)",
    r"\(album version\)",
    r"\(original song[^)]*\)",
    r"\(clean edit\)",
    r"\(cleaned\)",
    r"\(explicit\)",
    r"\(remastered\)",
)


class SyncError(Exception):
    pass


def load_playlist_ids(raw):
    if not raw:
        raise SyncError("Set SPOTIFY_PLAYLIST_IDS to a JSON object mapping divisions to playlist IDs.")
    try:
        playlists = json.loads(raw)
    except ValueError:
        raise SyncError("SPOTIFY_PLAYLIST_IDS must contain valid JSON.") from None
    if not isinstance(playlists, dict) or playlists.keys() != DIVISIONS.keys():
        raise SyncError("SPOTIFY_PLAYLIST_IDS must contain exactly the six division names.")
    for division, playlist_id in playlists.items():
        if not isinstance(playlist_id, str) or not re.fullmatch(r"[A-Za-z0-9]{22}", playlist_id):
            raise SyncError(f"SPOTIFY_PLAYLIST_IDS has an invalid playlist ID for {division}.")
    if len(set(playlists.values())) != len(DIVISIONS):
        raise SyncError("SPOTIFY_PLAYLIST_IDS must use a different playlist for each division.")
    return playlists


def cell_text(value):
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def build_lists(rows):
    lists = {division: [] for division in DIVISIONS}
    seen = {division: set() for division in DIVISIONS}
    labels = set()
    for artist, title, label in rows:
        if not isinstance(label, str):
            continue
        if label.strip():
            labels.add(label.strip())
        if not artist or not title:
            continue
        key = f"{cell_text(artist)} – {cell_text(title)}"
        for division, keywords in DIVISIONS.items():
            if key not in seen[division] and any(word in label.lower() for word in keywords):
                seen[division].add(key)
                lists[division].append([artist, title])
    return lists, sorted(labels, key=lambda label: label.encode("utf-16-be"))


def legacy_search_title(title):
    for pattern in LEGACY_TRACK_SUFFIXES:
        title = re.sub(r"\s*" + pattern, "", title, count=1, flags=re.IGNORECASE)
    return title.strip()


def checked_json(response, operation):
    if not response.ok:
        if operation.startswith("Spotify") and response.status_code in (400, 401, 403) and (
            operation == "Spotify token refresh" or response.status_code in (401, 403)
        ):
            raise SyncError(
                f"{operation} failed (HTTP {response.status_code}). "
                "Check Spotify authorization, app access and playlist permissions."
            )
        raise SyncError(f"{operation} failed (HTTP {response.status_code}).")
    return response.json()


class Sheets:
    def __init__(self, session):
        self.session = session

    def request(self, spreadsheet, method, path, operation, **kwargs):
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet}{path}"
        return checked_json(self.session.request(method, url, timeout=60, **kwargs), operation)

    def tabs(self, spreadsheet):
        data = self.request(spreadsheet, "GET", "", "Read Sheets tab names",
                            params={"fields": "sheets(properties(title))"})
        return {sheet["properties"]["title"] for sheet in data["sheets"]}

    def read(self, spreadsheet, ranges, *, render_option="UNFORMATTED_VALUE"):
        if not ranges:
            return []
        data = self.request(
            spreadsheet, "GET", "/values:batchGet", "Read Sheets values",
            params={"ranges": ranges, "valueRenderOption": render_option},
        )
        return [item.get("values", []) for item in data["valueRanges"]]

    def write(self, spreadsheet, values):
        if values:
            self.request(
                spreadsheet, "POST", "/values:batchUpdate", "Write Sheets values",
                json={"valueInputOption": "RAW", "data": [
                    {"range": address, "values": rows} for address, rows in values.items()
                ]},
            )

    def append_failures(self, spreadsheet, rows):
        if rows:
            self.request(
                spreadsheet, "POST", f"/values/{quote(FAILED + '!A:D', safe='')}:append",
                "Record Failed Searches", params={"valueInputOption": "RAW",
                                                   "insertDataOption": "INSERT_ROWS"},
                json={"values": rows},
            )


class Spotify:
    def __init__(self, session, *, legacy_fallback=False):
        self.session = session
        self.legacy_fallback = legacy_fallback
        self.access_token = None

    def authorize(self):
        response = self.session.post(
            "https://accounts.spotify.com/api/token",
            auth=(os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"]),
            data={"grant_type": "refresh_token", "refresh_token": os.environ["SPOTIFY_REFRESH_TOKEN"]},
            timeout=30,
        )
        self.access_token = checked_json(response, "Spotify token refresh")["access_token"]

    def request(self, method, path, operation, **kwargs):
        if self.access_token is None:
            self.authorize()
        return checked_json(
            self.session.request(
                method, f"https://api.spotify.com/v1/{path}",
                headers={"Authorization": f"Bearer {self.access_token}"}, timeout=30, **kwargs,
            ),
            operation,
        )

    def search(self, artist, title):
        def api(method, path, **kwargs):
            return self.request(method, path.lstrip("/"), "Spotify track search", **kwargs)

        artist = cell_text(artist)
        matched = find_track(api, artist, title, "")
        if matched or not self.legacy_fallback:
            return matched

        legacy_title = legacy_search_title(title)
        if not legacy_title:
            return None
        result = api("GET", "/search", params={
            "q": f"track:{legacy_title} artist:{artist}", "type": "track", "limit": 1,
        })
        candidates = result["tracks"]["items"]
        if not candidates:
            return None
        logging.info("Legacy Spotify fallback: %s — %s", artist, title)
        return candidates[0]["uri"]

    def add(self, playlist, tracks):
        for start in range(0, len(tracks), 100):
            self.request("POST", f"playlists/{playlist}/items", "Spotify playlist append",
                         json={"uris": tracks[start:start + 100]})


def read_counters(state_rows):
    counters = {}
    for row_number, row in enumerate(state_rows[1:], start=2):
        if not row or row[0] not in DIVISIONS:
            continue
        division = row[0]
        try:
            value = float(row[1])
            if isinstance(row[1], bool) or not value.is_integer() or value < 1:
                raise ValueError
        except (IndexError, TypeError, ValueError):
            raise SyncError(f"{division} has an invalid saved row in Spotify Sync State.") from None
        if division in counters:
            raise SyncError(f"{division} has duplicate saved rows in Spotify Sync State.")
        counters[division] = (row_number, int(value))
    if missing := DIVISIONS.keys() - counters.keys():
        raise SyncError(f"Missing Spotify Sync State rows for {', '.join(sorted(missing))}.")
    return counters


def source_lists(sheets, source):
    years = sorted(name for name in sheets.tabs(source)
                   if re.fullmatch(r"[0-9]{4}", name) and int(name) >= 2012)
    if not years:
        raise SyncError("The source spreadsheet has no year tabs from 2012 onward.")
    headers = sheets.read(source, [f"'{year}'!1:1" for year in years])
    competition_columns = []
    ranges = []
    for year, header_rows in zip(years, headers):
        header = header_rows[0] if header_rows else []
        matches = [index for index, value in enumerate(header)
                   if isinstance(value, str) and value.strip().casefold() == "competition"]
        if len(matches) != 1:
            raise SyncError(f"Year tab {year} must have exactly one Competition header.")
        column = matches[0]
        competition_columns.append(column)
        end = max(column, 1) + 1
        letters = ""
        while end:
            end, remainder = divmod(end - 1, 26)
            letters = chr(ord("A") + remainder) + letters
        ranges.append(f"'{year}'!A2:{letters}")

    rows = []
    numeric_titles = []
    for year, column, tab in zip(years, competition_columns, sheets.read(source, ranges)):
        for index, row in enumerate(tab, 2):
            if len(row) <= column:
                continue
            artist, title = (row + [""])[:2]
            label = row[column]
            if artist and isinstance(label, str) and label.strip() and type(title) in (int, float):
                numeric_titles.append((len(rows), f"'{year}'!B{index}"))
            rows.append((artist, title, label))
    if numeric_titles:
        displayed = sheets.read(source, [address for _, address in numeric_titles],
                                render_option="FORMATTED_VALUE")
        for (index, _), cells in zip(numeric_titles, displayed):
            if cells and cells[0]:
                artist, _, label = rows[index]
                rows[index] = artist, cells[0][0], label
    return build_lists(rows)


def sync(sheets, spotify, source, output, *, playlist_ids=None, check=False, summary=False):
    if not (check or summary) and playlist_ids is None:
        raise SyncError("Spotify playlist targets are required for sync.")
    lists, labels = source_lists(sheets, source)
    if summary:
        if labels:
            sheets.write(output, {"'Summary'!A1": [[label] for label in labels]})
        logging.info("Summary labels: %d", len(labels))
        return

    ranges = {division: f"'{division}'!A2:B" for division in DIVISIONS}
    state_rows, *division_rows, _ = sheets.read(
        output, [f"{STATE}!A:B", *ranges.values(), f"{FAILED}!A1"],
    )
    counters = read_counters(state_rows)
    for division, old in zip(DIVISIONS, division_rows):
        _, last = counters[division]
        prefix_length = last - 1
        if (len(lists[division]) < prefix_length
                or lists[division][:prefix_length] != old[:prefix_length]):
            raise SyncError(f"{division}: rebuilt rows through saved sheet row {last} differ "
                            "from the existing division tab. Review before syncing.")
        if check:
            logging.info("%s: saved row %d, %d rebuilt rows after it",
                         division, last, len(lists[division]) - prefix_length)
    if check:
        return

    sheets.write(output, {ranges[division]: rows for division, rows in lists.items() if rows})
    # Re-read so existing tail rows are retained, as with Apps Script's in-place writes.
    refreshed = sheets.read(output, list(ranges.values()))
    for division, rows in zip(DIVISIONS, refreshed):
        state_row, last = counters[division]
        if last >= len(rows) + 1:
            logging.info("%s: no new rows", division)
            continue
        tracks, failures = [], []
        for row in rows[last - 1:]:
            if len(row) < 2 or not all(row[:2]):
                continue
            artist, title = row[:2]
            title = cell_text(title).strip()
            uri = spotify.search(artist, title)
            if uri:
                tracks.append(uri)
            else:
                failures.append([division, artist, title, datetime.now(timezone.utc).isoformat()])
        sheets.append_failures(output, failures)
        spotify.add(playlist_ids[division], tracks)
        sheets.write(output, {f"{STATE}!B{state_row}": [[len(rows) + 1]]})
        logging.info("%s: %d added, %d failed, saved row %d",
                     division, len(tracks), len(failures), len(rows) + 1)


def main():
    import requests
    from google.auth.exceptions import GoogleAuthError
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials

    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="Compare saved prefixes without writing")
    modes.add_argument("--summary", action="store_true", help="Refresh Summary only")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        playlist_ids = None
        if not (args.check or args.summary):
            playlist_ids = load_playlist_ids(os.environ.get("SPOTIFY_PLAYLIST_IDS"))
        credentials = Credentials.from_service_account_file(
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        with AuthorizedSession(credentials) as google, requests.Session() as spotify:
            matcher = Spotify(spotify, legacy_fallback=os.environ.get("SPOTIFY_LEGACY_FALLBACK") == "true")
            sync(Sheets(google), matcher, os.environ["SOURCE_SHEET_ID"],
                 os.environ["OUTPUT_SHEET_ID"], playlist_ids=playlist_ids,
                 check=args.check, summary=args.summary)
    except KeyError as error:
        logging.error("Missing configuration or API response field: %s", error.args[0])
        return 1
    except SyncError as error:
        logging.error("%s", error)
        return 1
    except (GoogleAuthError, requests.RequestException, OSError, ValueError) as error:
        logging.error("Job failed during configuration, authorization or an API request (%s).",
                      type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
