"""Refresh the Spotify playlist inventory and publish approved social sets."""

import argparse
import logging
import os
import re
from datetime import date
from functools import lru_cache
from urllib.parse import quote

import google.auth
import requests
from google.auth.transport.requests import AuthorizedSession

WORKBOOK_NAME = "SwingStation Sync"
TAB_NAME = "Spotify Playlists"
TAB = f"'{TAB_NAME}'"
HEADERS = ["Spotify Playlist ID", "Name", "Spotify URL", "Visibility", "Status", "Reason"]
PAUSED, APPROVED, DONE, ERROR, IGNORED = "⏸️", "▶️", "✅", "❌", "🚫"
COLUMN_WIDTHS = (205, 285, 105, 80, 72, 420)
VIEWS = (
    ("Needs Action", (PAUSED, ERROR), "No playlists need action"),
    ("Pending", (APPROVED,), "No pending playlists"),
    ("On SwingStation", (DONE,), "Nothing on SwingStation yet"),
)
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
    start_time = None
    suffix = re.search(r"\s+\((\d+:[^()]*)\)$", event)
    if suffix:
        time_match = re.fullmatch(r"(\d{1,2}):(\d{2})", suffix[1])
        if not time_match:
            raise ValueError("Use a 24-hour start time like (23:00)")
        hour, minute = map(int, time_match.groups())
        if hour > 23 or minute > 59:
            raise ValueError("Use a valid 24-hour start time like (23:00)")
        event = event[:suffix.start()]
        start_time = f"{hour:02d}:{minute:02d}"
    elif re.fullmatch(r"\(\d+:[^()]*\)", event):
        raise ValueError("Add an event name before the start time")
    return played, event, start_time


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


def workbook_setup_requests(sheet_id):
    requests = [
        {"updateSpreadsheetProperties": {
            "properties": {"title": WORKBOOK_NAME}, "fields": "title",
        }},
        {"updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "title": TAB_NAME,
                "gridProperties": {"frozenRowCount": 1},
            },
            "fields": "title,gridProperties.frozenRowCount",
        }},
    ]
    tabs = [(sheet_id, None)]
    view_ids = (candidate for candidate in range(len(VIEWS) + 1) if candidate != sheet_id)
    for name, statuses, empty_message in VIEWS:
        view_id = next(view_ids)
        requests.append({"addSheet": {"properties": {
            "sheetId": view_id, "title": name, "gridProperties": {"frozenRowCount": 1},
        }}})
        condition = "+".join(f'({TAB}!E:E="{status}")' for status in statuses)
        formula = f'=IFNA(FILTER({TAB}!A:F,{condition}),"{empty_message}")'
        tabs.append((view_id, formula))

    for tab_id, formula in tabs:
        header = {
            "values": [
                {"userEnteredValue": {"stringValue": title}, "userEnteredFormat": {
                    "textFormat": {"bold": True},
                    "backgroundColor": {"red": 0.85, "green": 0.90, "blue": 0.98},
                }}
                for title in HEADERS
            ],
        }
        rows = [header]
        if formula:
            rows.append({"values": [{"userEnteredValue": {"formulaValue": formula}}]})
        requests.append({"updateCells": {
            "start": {"sheetId": tab_id, "rowIndex": 0, "columnIndex": 0},
            "rows": rows,
            "fields": "userEnteredValue,userEnteredFormat",
        }})
        for column, width in enumerate(COLUMN_WIDTHS):
            requests.append({"updateDimensionProperties": {
                "range": {
                    "sheetId": tab_id, "dimension": "COLUMNS",
                    "startIndex": column, "endIndex": column + 1,
                },
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }})
        status_range = {
            "sheetId": tab_id, "startRowIndex": 1, "startColumnIndex": 4, "endColumnIndex": 5,
        }
        requests.append({"repeatCell": {
            "range": status_range,
            "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat.horizontalAlignment",
        }})
        if formula is None:
            requests.append({"setDataValidation": {
                "range": status_range,
                "rule": {
                    "condition": {"type": "ONE_OF_LIST", "values": [
                        {"userEnteredValue": status}
                        for status in (PAUSED, APPROVED, DONE, ERROR, IGNORED)
                    ]},
                    "strict": True,
                    "showCustomUi": True,
                },
            }})
    return requests


class PlaylistSheet:
    def __init__(self, session, spreadsheet):
        self.session = session
        self.url = f"https://sheets.googleapis.com/v4/spreadsheets/{quote(spreadsheet, safe='')}"

    def initialize_empty_workbook(self):
        response = self.session.get(
            self.url,
            params={"fields": "sheets(properties(sheetId,title,sheetType,gridProperties))"},
            timeout=TIMEOUT,
        )
        sheets = checked_json(response, "Inspect workbook for first-run setup")["sheets"]
        if len(sheets) != 1 or sheets[0]["properties"].get("sheetType") != "GRID":
            raise RuntimeError("Workbook setup requires exactly one empty grid tab")
        properties = sheets[0]["properties"]
        title = "'" + properties["title"].replace("'", "''") + "'"
        response = self.session.get(
            f"{self.url}/values/{quote(title, safe='')}",
            params={"valueRenderOption": "FORMULA"},
            timeout=TIMEOUT,
        )
        if checked_json(response, "Check the entire tab for existing data").get("values"):
            raise RuntimeError("Workbook setup requires an empty tab")

        sheet_id = properties["sheetId"]
        grid = properties["gridProperties"]
        requests = workbook_setup_requests(sheet_id)
        for dimension, count, minimum in (
            ("ROWS", grid["rowCount"], 1000), ("COLUMNS", grid["columnCount"], len(HEADERS))
        ):
            if count < minimum:
                requests.insert(0, {"appendDimension": {
                    "sheetId": sheet_id, "dimension": dimension, "length": minimum - count,
                }})
        response = self.session.post(
            f"{self.url}:batchUpdate", json={"requests": requests}, timeout=TIMEOUT,
        )
        checked_json(response, "Initialize SwingStation Sync workbook")

    def read(self, *, initialize=False):
        response = self.session.get(f"{self.url}/values/{quote(TAB + '!A:F', safe='')}", timeout=TIMEOUT)
        if initialize and response.status_code == 400:
            self.initialize_empty_workbook()
            return True, {}
        rows = checked_json(response, "Read Spotify Playlists tab").get("values", [])
        if initialize and not rows:
            self.initialize_empty_workbook()
            return True, {}
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

    def insert_at_top(self, rows):
        if not rows:
            return
        response = self.session.get(
            self.url,
            params={"ranges": TAB, "fields": "sheets(properties(sheetId))"},
            timeout=TIMEOUT,
        )
        sheet = checked_json(response, "Find Spotify Playlists tab")["sheets"][0]["properties"]
        sheet_id = sheet["sheetId"]
        end_row = 1 + len(rows)
        columns = {"sheetId": sheet_id, "startColumnIndex": 0, "endColumnIndex": len(HEADERS)}
        # After insertion, the former first body row sits just below the new block.
        source = {**columns, "startRowIndex": end_row, "endRowIndex": end_row + 1}
        destination = {**columns, "startRowIndex": 1, "endRowIndex": end_row}
        response = self.session.post(
            f"{self.url}:batchUpdate",
            json={"requests": [
                {"insertDimension": {
                    "range": {
                        "sheetId": sheet_id, "dimension": "ROWS", "startIndex": 1, "endIndex": end_row,
                    },
                    "inheritFromBefore": False,
                }},
                *({"copyPaste": {
                    "source": source,
                    "destination": destination,
                    "pasteType": paste_type,
                }} for paste_type in (
                    "PASTE_FORMAT", "PASTE_DATA_VALIDATION", "PASTE_CONDITIONAL_FORMATTING",
                )),
                {"updateCells": {
                    "start": {"sheetId": sheet_id, "rowIndex": 1, "columnIndex": 0},
                    "rows": [
                        {"values": [{"userEnteredValue": {"stringValue": value}} for value in row]}
                        for row in rows
                    ],
                    "fields": "userEnteredValue",
                }},
            ]},
            timeout=TIMEOUT,
        )
        checked_json(response, "Insert and format new Spotify playlists")


def initial_status(title, visibility, get_events):
    if visibility == "Private":
        return IGNORED, "Private playlist"
    if visibility != "Public" or get_events is None:
        return PAUSED, ""

    try:
        _, event_name, _ = parse_title(title)
    except ValueError:
        if re.match(r"^\d{1,4}\s*[./-]\s*\d|^\d{8}(?:\s|$)", title.strip()):
            return PAUSED, "Check date format or event name in playlist title"
        return IGNORED, "No set date in title"

    matches = get_events().get(normalize_name(event_name), [])
    if not matches:
        return PAUSED, EVENT_MISSING + event_name
    if len(matches) > 1:
        return PAUSED, f"Multiple SwingStation events match: {event_name}"
    if matches[0]["type"] != "local event":
        return PAUSED, "Event type is not eligible for auto-approval"
    return APPROVED, ""


def refresh_inventory(sheet, playlists, get_auto_approval_events=None):
    _, existing = sheet.read(initialize=True)
    updates = []
    additions = []
    for playlist_id, metadata in playlists.items():
        if playlist_id in existing:
            number, row = existing[playlist_id]
            if row[1:4] != metadata:
                updates.append({"range": f"{TAB}!B{number}:D{number}", "values": [metadata]})
            continue
        title, _, visibility = metadata
        status, reason = initial_status(title, visibility, get_auto_approval_events)
        additions.append([playlist_id, *metadata, status, reason])
    sheet.write(updates)
    sheet.insert_at_top(additions)
    logging.info("Refreshed %d owned Spotify playlists, including %d new rows", len(playlists), len(additions))


def event_index(session):
    response = session.get(f"{SWINGSTATION}/events", timeout=TIMEOUT)
    events = checked_json(response, "List SwingStation events")["events"]
    by_name = {}
    for event in events:
        by_name.setdefault(normalize_name(event["name"]), []).append(event)
    return by_name


def publish(session, api_key, event, played, playlist_url, start_time=None):
    if event["type"] not in {"local event", "convention"}:
        return ERROR, "Unsupported SwingStation event type"
    if event["type"] == "convention" and start_time is None:
        return ERROR, "Add a start time to the playlist title, e.g. (23:00)"
    payload = {"spotify_url": playlist_url, "event_id": event["id"], "date": played.isoformat()}
    if start_time is not None:
        payload["start_time"] = start_time
    try:
        response = session.post(
            f"{SWINGSTATION}/sets",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
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
        error = response.json().get("error", {})
    except ValueError:
        error = {}
    code = error.get("code", "")
    if response.status_code == 409 and code == "already_uploaded":
        return DONE, ""
    if code == "event_not_found":
        return ERROR, EVENT_MISSING + event["name"]
    if code == "missing_field" and "start_time is required" in error.get("message", ""):
        return ERROR, "SwingStation requires a start time for convention socials"
    if code and re.fullmatch(r"[a-z0-9_]+", code):
        return ERROR, f"SwingStation upload failed: {code} (HTTP {response.status_code})"
    return ERROR, f"SwingStation upload failed (HTTP {response.status_code})"


def process_queue(sheet, session, api_key, playlists, get_events):
    _, rows = sheet.read()
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
                played, event_name, start_time = parse_title(metadata[0])
            except ValueError as exc:
                outcome = ERROR, str(exc)
            else:
                matches = get_events().get(normalize_name(event_name), [])
                if not matches:
                    outcome = ERROR, EVENT_MISSING + event_name
                elif len(matches) > 1:
                    outcome = ERROR, f"Multiple SwingStation events match: {event_name}"
                else:
                    outcome = publish(session, api_key, matches[0], played, metadata[1], start_time)
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

            @lru_cache(maxsize=1)
            def get_events():
                return event_index(session)

            auto_approve = mode == "true" and not args.inventory_only
            refresh_inventory(sheet, playlists, get_events if auto_approve else None)
            if not args.inventory_only:
                process_queue(sheet, session, api_key, playlists, get_events)
    except KeyError as exc:
        raise SystemExit(f"Missing required configuration or response field: {exc}") from None
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    except requests.RequestException as exc:
        raise SystemExit(f"Service request failed: {type(exc).__name__}") from None


if __name__ == "__main__":
    main()
