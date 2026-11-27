"""Refresh the Spotify playlist inventory and publish approved social sets."""

import argparse
import logging
import os
import re
from datetime import date
from urllib.parse import quote

import google.auth
import requests
from google.auth.transport.requests import AuthorizedSession

TAB_NAME = "Spotify Playlists"
TAB = f"'{TAB_NAME}'"
HEADERS = ["Spotify Playlist ID", "Name", "Spotify URL", "Visibility", "Status", "Reason"]
PAUSED, APPROVED, DONE, ERROR, IGNORED = "⏸️", "▶️", "✅", "❌", "🚫"
EVENT_MISSING = "Event not found: "
SPOTIFY = "https://api.spotify.com/v1"
SWINGSTATION = "https://www.swingstation.app/api/v1"
TIMEOUT = 30
TITLE = re.compile(
    r"^(?:(?P<year_first>\d{4})(?P<separator>[.-])(?P<month_first>\d{2})"
    r"(?P=separator)\s*(?P<day_last>\d{2})|"
    r"(?P<day_first>\d{2})\.(?P<month_last>\d{2})\.(?P<year_last>\d{4}))"
    r"\s+(?P<event>\S.*)$"
)


def parse_title(title):
    match = TITLE.fullmatch(title.strip())
    if not match:
        raise ValueError("Expected a playlist title starting with a date and event name")
    event = match["event"].strip()
    if re.match(r"^(?:[-~–—]|to)\s*\d|^\d{4}[.-]\d{2}[.-]", event, re.IGNORECASE):
        raise ValueError("Date ranges in playlist titles are unsupported")
    try:
        played = date(
            int(match["year_first"] or match["year_last"]),
            int(match["month_first"] or match["month_last"]),
            int(match["day_first"] or match["day_last"]),
        )
    except ValueError as exc:
        raise ValueError("Invalid calendar date in playlist title") from exc
    return played, event


def normalize_name(name):
    return " ".join(name.casefold().split())


def checked_json(response, operation):
    if not response.ok:
        retry = response.headers.get("Retry-After")
        detail = f" (Retry-After: {retry} seconds)" if retry else ""
        raise RuntimeError(f"{operation} returned HTTP {response.status_code}{detail}")
    return response.json()


def list_owned_playlists(session, client_id, refresh_token):
    # The dedicated app was authorized with PKCE, so refresh uses its public client ID.
    token = checked_json(
        session.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh_token},
            timeout=TIMEOUT,
        ),
        "Spotify token refresh",
    )
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    account = checked_json(
        session.get(f"{SPOTIFY}/me", headers=headers, timeout=TIMEOUT), "Spotify profile"
    )["id"]
    playlists = {}
    url = f"{SPOTIFY}/me/playlists?limit=50"
    while url:
        page = checked_json(
            session.get(url, headers=headers, timeout=TIMEOUT), "Spotify playlist listing"
        )
        for item in page["items"]:
            if item and item["owner"]["id"] == account:
                visibility = {True: "Public", False: "Private"}.get(item["public"], "Unknown")
                playlists[item["id"]] = [
                    item["name"], item["external_urls"]["spotify"], visibility
                ]
        url = page["next"]
    return playlists


class PlaylistSheet:
    def __init__(self, session, spreadsheet):
        self.session = session
        self.url = f"https://sheets.googleapis.com/v4/spreadsheets/{quote(spreadsheet, safe='')}"

    def read(self):
        response = self.session.get(f"{self.url}/values/{quote(TAB + '!A:F', safe='')}", timeout=TIMEOUT)
        rows = checked_json(response, "Read Spotify Playlists tab").get("values", [])
        if rows and rows[0] != HEADERS:
            raise RuntimeError("Spotify Playlists headers do not match the expected columns")
        by_id = {}
        for number, row in enumerate(rows[1:], 2):
            if not any(row):
                continue
            row = row + [""] * (len(HEADERS) - len(row))
            if not row[0] or row[0] in by_id:
                raise RuntimeError(f"Missing or duplicate Spotify playlist ID on row {number}")
            by_id[row[0]] = (number, row)
        return bool(rows), by_id

    def write(self, updates):
        if updates:
            response = self.session.post(
                f"{self.url}/values:batchUpdate",
                json={"valueInputOption": "RAW", "data": updates},
                timeout=TIMEOUT,
            )
            checked_json(response, "Update Spotify Playlists tab")

    def insert_at_top_and_center_status(self, rows):
        response = self.session.get(
            self.url,
            params={"ranges": TAB, "fields": "sheets(properties(sheetId))"},
            timeout=TIMEOUT,
        )
        sheet = checked_json(response, "Find Spotify Playlists tab")["sheets"][0]["properties"]
        sheet_id = sheet["sheetId"]
        updates = []
        if rows:
            new_rows = {
                "sheetId": sheet_id, "dimension": "ROWS", "startIndex": 1, "endIndex": 1 + len(rows)
            }
            updates.extend([
                {"insertDimension": {"range": new_rows, "inheritFromBefore": False}},
                {"updateCells": {
                    "start": {"sheetId": sheet_id, "rowIndex": 1, "columnIndex": 0},
                    "rows": [
                        {"values": [{"userEnteredValue": {"stringValue": value}} for value in row]}
                        for row in rows
                    ],
                    "fields": "userEnteredValue",
                }},
            ])
        status_column = HEADERS.index("Status")
        updates.append({"repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "startColumnIndex": status_column,
                "endColumnIndex": status_column + 1,
            },
            "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat.horizontalAlignment",
        }})
        response = self.session.post(
            f"{self.url}:batchUpdate",
            json={"requests": updates},
            timeout=TIMEOUT,
        )
        checked_json(response, "Insert new Spotify playlists and center statuses")


def refresh_inventory(sheet, playlists, auto_approve):
    has_header, existing = sheet.read()
    updates = [] if has_header else [{"range": f"{TAB}!A1:F1", "values": [HEADERS]}]
    additions = []
    for playlist_id, metadata in playlists.items():
        if playlist_id in existing:
            number, row = existing[playlist_id]
            if row[1:4] != metadata:
                updates.append({"range": f"{TAB}!B{number}:D{number}", "values": [metadata]})
            continue
        visibility = metadata[2]
        if visibility == "Private":
            status, reason = IGNORED, "Private playlist"
        else:
            status = APPROVED if auto_approve and visibility == "Public" else PAUSED
            reason = ""
        additions.append([playlist_id, *metadata, status, reason])
    sheet.write(updates)
    sheet.insert_at_top_and_center_status(additions)
    logging.info("Refreshed %d owned Spotify playlists, including %d new rows", len(playlists), len(additions))


def event_index(session):
    response = session.get(f"{SWINGSTATION}/events", timeout=TIMEOUT)
    events = checked_json(response, "List SwingStation events")["events"]
    by_name = {}
    for event in events:
        by_name.setdefault(normalize_name(event["name"]), []).append(event)
    return by_name


def publish(session, api_key, event, played, playlist_url):
    if event["type"] == "convention":
        return ERROR, "Convention upload unavailable until SwingStation's date requirements are verified"
    if event["type"] != "local event":
        return ERROR, "Unsupported SwingStation event type"
    try:
        response = session.post(
            f"{SWINGSTATION}/sets",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"spotify_url": playlist_url, "event_id": event["id"], "date": played.isoformat()},
            timeout=TIMEOUT,
        )
    except (requests.Timeout, requests.ConnectionError):
        return None
    if response.status_code == 201:
        return DONE, ""
    if response.status_code == 429 or response.status_code >= 500:
        logging.warning("SwingStation upload deferred (HTTP %s)", response.status_code)
        return None
    try:
        code = response.json().get("error", {}).get("code", "")
    except ValueError:
        code = ""
    if response.status_code == 409 and code == "already_uploaded":
        return DONE, ""
    if code == "event_not_found":
        return ERROR, EVENT_MISSING + event["name"]
    if code and re.fullmatch(r"[a-z0-9_]+", code):
        return ERROR, f"SwingStation upload failed: {code} (HTTP {response.status_code})"
    return ERROR, f"SwingStation upload failed (HTTP {response.status_code})"


def process_queue(sheet, session, api_key, playlists):
    _, rows = sheet.read()
    events = None
    for playlist_id, (number, row) in rows.items():
        status, reason = row[4:6]
        if status != APPROVED and not (status == ERROR and reason.startswith(EVENT_MISSING)):
            continue
        metadata = playlists.get(playlist_id)
        if metadata is None:
            outcome = ERROR, "Playlist is no longer in the owned Spotify inventory"
        elif metadata[2] != "Public":
            outcome = ERROR, "Make the playlist public before publishing"
        else:
            try:
                played, event_name = parse_title(metadata[0])
            except ValueError as exc:
                outcome = ERROR, str(exc)
            else:
                if events is None:
                    events = event_index(session)
                matches = events.get(normalize_name(event_name), [])
                if not matches:
                    outcome = ERROR, EVENT_MISSING + event_name
                elif len(matches) > 1:
                    outcome = ERROR, f"Multiple SwingStation events match: {event_name}"
                else:
                    outcome = publish(session, api_key, matches[0], played, metadata[1])
                    if outcome is None:
                        return
        if list(outcome) != row[4:6]:
            sheet.write([{"range": f"{TAB}!E{number}:F{number}", "values": [list(outcome)]}])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-only", action="store_true", help="Refresh Spotify and Sheets only")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    mode = os.getenv("AUTO_APPROVE", "false").strip().lower()
    if mode not in {"true", "false"}:
        raise SystemExit("AUTO_APPROVE must be true or false")
    try:
        spreadsheet = os.environ["SPOTIFY_PLAYLIST_SPREADSHEET_ID"]
        api_key = None if args.inventory_only else os.environ["SWINGSTATION_API_KEY"]
        client_id = os.environ["SPOTIFY_CLIENT_ID"]
        refresh_token = os.environ["SPOTIFY_REFRESH_TOKEN"]
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
        with AuthorizedSession(credentials) as google_session, requests.Session() as session:
            sheet = PlaylistSheet(google_session, spreadsheet)
            playlists = list_owned_playlists(session, client_id, refresh_token)
            refresh_inventory(sheet, playlists, mode == "true")
            if not args.inventory_only:
                process_queue(sheet, session, api_key, playlists)
    except KeyError as exc:
        raise SystemExit(f"Missing required configuration or response field: {exc}") from None
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    except requests.RequestException as exc:
        raise SystemExit(f"Service request failed: {type(exc).__name__}") from None


if __name__ == "__main__":
    main()
