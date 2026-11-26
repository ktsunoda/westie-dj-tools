"""Google Drive source and yearly Google Sheets output."""

from datetime import date
import re
from secrets import randbelow

DRIVE = "https://www.googleapis.com/drive/v3/files"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
HEADERS = [
    "Artist", "Song", "Duration", "Date", "Venue", "Event Type", "City",
    "State/Province", "Country/Region", "Competition", "Spotify", "Row #",
]


class DriveFolder:
    def __init__(self, session, folder_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id):
            raise ValueError("Invalid Drive folder ID")
        self.session = session
        self.folder_id = folder_id

    def files(self):
        params = {
            "q": f"'{self.folder_id}' in parents and trashed = false",
            "fields": "nextPageToken,files(id,name,mimeType)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        while True:
            response = self.session.get(DRIVE, params=params, timeout=30)
            response.raise_for_status()
            page = response.json()
            for item in page["files"]:
                if not item["mimeType"].startswith("application/vnd.google-apps."):
                    yield item["id"], item["name"]
            if "nextPageToken" not in page:
                return
            params["pageToken"] = page["nextPageToken"]

    def download(self, file_id):
        response = self.session.get(
            f"{DRIVE}/{file_id}", params={"alt": "media", "supportsAllDrives": "true"},
            timeout=30,
        )
        response.raise_for_status()
        return response.content.decode("utf-8-sig")

    def rename(self, file_id, name):
        response = self.session.patch(
            f"{DRIVE}/{file_id}", params={"supportsAllDrives": "true", "fields": "name"},
            json={"name": name}, timeout=30,
        )
        response.raise_for_status()
        if response.json()["name"] != name:
            raise ValueError("Drive returned a different filename")


def grid(sheet, start_row, end_row, start_column=0, end_column=12):
    return {
        "sheetId": sheet, "startRowIndex": start_row, "endRowIndex": end_row,
        "startColumnIndex": start_column, "endColumnIndex": end_column,
    }


def copy_cells(source, destination, paste_type):
    return {"copyPaste": {
        "source": source, "destination": destination, "pasteType": paste_type,
    }}


def song_key(song, artist):
    return tuple(" ".join(value.casefold().split()) for value in (song, artist))


def bumper_song_keys(rows):
    headers = [str(value).strip().casefold() for value in rows[0]] if rows else []
    if headers.count("song") != 1 or headers.count("artist") != 1:
        raise ValueError("Bumper Music needs one Song header and one Artist header in row 1")
    song_column = headers.index("song")
    artist_column = headers.index("artist")
    return {
        song_key(row[song_column], row[artist_column])
        for row in rows[1:]
        if len(row) > max(song_column, artist_column)
        and row[song_column] and row[artist_column]
    }


class HistorySheet:
    def __init__(self, session, workbook_id):
        self.session = session
        self.url = f"{SHEETS}/{workbook_id}"

    def read(self, path="", **params):
        response = self.session.get(self.url + path, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def __call__(self, playlist, tracks):
        year = str(playlist.played_on.year)
        sheets = self.read(fields="sheets(properties(sheetId,title,gridProperties))")
        tabs = {s["properties"]["title"]: s["properties"] for s in sheets["sheets"]}
        current = tabs.get(year)
        source = current or tabs.get(str(int(year) - 1))
        if source is None:
            raise ValueError("Previous year template is missing")
        source_id = source["sheetId"]
        source_year = source["title"]
        values = self.read(
            "/values:batchGet",
            ranges=[f"'{source_year}'!A1:B", f"'{source_year}'!A1:L2",
                    "'Event Details'!B2:B", "'Bumper Music'"],
            valueRenderOption="FORMULA",
        )
        existing, layout, events, bumper = [
            item.get("values", []) for item in values["valueRanges"]
        ]
        if (len(layout) < 2 or layout[0] != HEADERS or len(layout[1]) < len(HEADERS)
                or any(not str(layout[1][i]).startswith("=") for i in (5, 6, 7, 8, 11))):
            raise ValueError("Year tab template differs from the expected layout")
        venues = {row[0].strip().casefold(): row[0] for row in events if row}
        venue_key = playlist.event.casefold()
        if venue_key not in venues and venue_key == "asc":
            venue_key = "atlanta swing classic"
        venue = venues.get(venue_key, playlist.event)
        bumpers = bumper_song_keys(bumper)
        day = (playlist.played_on - date(1899, 12, 30)).days
        first = max(len(existing), 1) if current else 1
        end = first + len(tracks)
        requests = []
        if current:
            target = source_id
            rows = source["gridProperties"]["rowCount"]
            if end > rows:
                requests.append({"appendDimension": {
                    "sheetId": target, "dimension": "ROWS", "length": end - rows,
                }})
        else:
            ids = {tab["sheetId"] for tab in tabs.values()}
            target = randbelow(2_000_000_000) + 1
            while target in ids:
                target = randbelow(2_000_000_000) + 1
            dimensions = self.read(
                ranges=[f"'{source_year}'!A1:L2"],
                fields="sheets(data(columnMetadata(pixelSize),rowMetadata(pixelSize)))",
            )["sheets"][0]["data"][0]
            requests.append({"addSheet": {"properties": {
                "sheetId": target, "title": year, "index": 0,
                "gridProperties": {"rowCount": max(1000, end), "columnCount": 12,
                                   "frozenRowCount": source["gridProperties"].get("frozenRowCount", 0)},
            }}})
            requests.append(copy_cells(grid(source_id, 0, 1), grid(target, 0, 1), "PASTE_NORMAL"))
            for dimension, metadata in (("COLUMNS", "columnMetadata"), ("ROWS", "rowMetadata")):
                for index, item in enumerate(dimensions.get(metadata, [])):
                    if "pixelSize" not in item:
                        continue
                    last = end if dimension == "ROWS" and index == 1 else index + 1
                    requests.append({"updateDimensionProperties": {
                        "range": {"sheetId": target, "dimension": dimension,
                                  "startIndex": index, "endIndex": last},
                        "properties": item, "fields": "pixelSize",
                    }})
        requests.append(copy_cells(grid(source_id, 1, 2), grid(target, first, end), "PASTE_FORMAT"))
        rows = []
        matches = []
        for offset, track in enumerate(tracks):
            artist, song = track["artist"], track["title"]
            minutes, seconds = divmod(round(track["duration_seconds"]), 60)
            row = [{"stringValue": artist}, {"stringValue": song},
                   {"stringValue": f"{minutes}:{seconds:02d}"}, {"numberValue": day},
                   {"stringValue": venue}]
            rows.append({"values": [{"userEnteredValue": cell} for cell in row]})
            if song_key(song, artist) in bumpers:
                matches.append(first + offset)
        requests.append({"updateCells": {
            "start": {"sheetId": target, "rowIndex": first, "columnIndex": 0},
            "rows": rows, "fields": "userEnteredValue",
        }})
        for start_column, end_column in ((5, 9), (11, 12)):
            requests.append(copy_cells(
                grid(source_id, 1, 2, start_column, end_column),
                grid(target, first, end, start_column, end_column), "PASTE_FORMULA",
            ))
        for row in matches:
            requests.append({"repeatCell": {
                "range": grid(target, row, row + 1),
                "cell": {"userEnteredFormat": {"backgroundColorStyle": {
                    "rgbColor": {"red": 1, "green": 0.9, "blue": 0.68},
                }}},
                "fields": "userEnteredFormat.backgroundColorStyle",
            }})
        response = self.session.post(self.url + ":batchUpdate", json={"requests": requests}, timeout=30)
        response.raise_for_status()
