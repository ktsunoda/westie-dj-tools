"""Read track overrides and record search misses in a shared Google Sheet."""

import hashlib
import json
import os
import re

TAB = "Track Overrides"
HEADERS = ("Artist", "Title", "Spotify Track URL")
STATUS_HEADER = "Status"
TRACK_URL = re.compile(r"https://open\.spotify\.com/(?:intl-[a-zA-Z-]+/)?track/([A-Za-z0-9]{22})(?:[?#].*)?")


class TrackOverridesError(ValueError):
    """The shared override workbook could not be read or updated."""


class TrackOverrides:
    def __init__(self, session, spreadsheet_id):
        self.session = session
        self.url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
        self.sheet_id = None
        self.columns = {}
        self.mapping = {}
        self.pairs = set()

    @classmethod
    def from_env(cls, session):
        workbook_id = os.environ.get("SPOTIFY_SEARCH_SPREADSHEET_ID")
        return cls(session, workbook_id) if workbook_id else None

    def load(self, *, refresh=False):
        if self.sheet_id is not None and not refresh:
            return self.mapping
        response = self.session.get(self.url, params={
            "ranges": f"'{TAB}'",
            "fields": "sheets(properties(sheetId,title),data(rowData(values(formattedValue))))",
        }, timeout=30)
        if not response.ok:
            raise TrackOverridesError(f"Reading {TAB} returned HTTP {response.status_code}")
        sheets = response.json().get("sheets", [])
        if len(sheets) != 1 or sheets[0]["properties"]["title"] != TAB:
            raise TrackOverridesError(f"Expected a tab named {TAB}")
        sheet = sheets[0]
        rows = [
            [cell.get("formattedValue", "") for cell in row.get("values", [])]
            for row in sheet["data"][0].get("rowData", [])
        ]
        header = rows[0] if rows else []
        if any(header.count(name) != 1 for name in HEADERS) or header.count(STATUS_HEADER) > 1:
            raise TrackOverridesError(f"{TAB} has unexpected headers")
        columns = {name: header.index(name) for name in HEADERS}
        pairs, mapping = set(), {}
        for index, row in enumerate(rows[1:], 2):
            artist, title, url = (
                row[columns[name]] if columns[name] < len(row) else "" for name in HEADERS
            )
            if not artist.strip() or not title.strip():
                continue
            key = (artist, title)
            pairs.add(key)
            if url.strip():
                match = TRACK_URL.fullmatch(url.strip())
                if not match:
                    raise TrackOverridesError(f"Invalid Spotify track URL in {TAB} row {index}")
                uri = f"spotify:track:{match[1]}"
                if mapping.setdefault(key, uri) != uri:
                    raise TrackOverridesError(f"Conflicting Spotify track URLs in {TAB} row {index}")
        if STATUS_HEADER in header:
            columns[STATUS_HEADER] = header.index(STATUS_HEADER)
        self.columns = columns
        self.mapping = mapping
        self.pairs = pairs
        self.sheet_id = sheet["properties"]["sheetId"]
        return self.mapping

    def resolve_miss(self, artist, title):
        """Return the exact pair's override or queue an unknown pair for review."""
        self.load()
        key = (artist, title)
        if not artist.strip() or not title.strip() or key in self.pairs:
            return self.mapping.get(key)
        value = json.dumps(key, ensure_ascii=False)
        digest = hashlib.sha256(value.encode()).digest()
        base_id = int.from_bytes(digest[:4], "big")
        row = {"sheetId": self.sheet_id, "dimension": "ROWS", "startIndex": 1, "endIndex": 2}
        for offset in range(5):
            cells = {"Artist": artist, "Title": title}
            status_format = []
            if STATUS_HEADER in self.columns:
                cells[STATUS_HEADER] = "⏳"
                column = self.columns[STATUS_HEADER]
                status_format = [{"repeatCell": {
                    "range": {
                        "sheetId": self.sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                        "startColumnIndex": column, "endColumnIndex": column + 1,
                    },
                    "cell": {"userEnteredFormat": {
                        "horizontalAlignment": "CENTER",
                    }},
                    "fields": "userEnteredFormat.horizontalAlignment",
                }}]
            metadata_id = (base_id + offset) % (2**31 - 1) + 1
            # The row and its unique key are created atomically. Deleting the row
            # in Sheets also removes its metadata so the pair can be logged again.
            metadata = {
                "metadataId": metadata_id, "metadataKey": "spotify_search_pair",
                "metadataValue": value, "visibility": "DOCUMENT",
                "location": {"dimensionRange": row},
            }
            response = self.session.post(f"{self.url}:batchUpdate", json={"requests": [
                {"insertDimension": {"range": row, "inheritFromBefore": False}},
                *({"updateCells": {
                    "start": {"sheetId": self.sheet_id, "rowIndex": 1, "columnIndex": self.columns[name]},
                    "rows": [{"values": [{"userEnteredValue": {"stringValue": text}}]}],
                    "fields": "userEnteredValue",
                }} for name, text in cells.items()),
                *status_format,
                {"createDeveloperMetadata": {"developerMetadata": metadata}},
            ]}, timeout=30)
            if response.ok:
                break
            if response.status_code not in (400, 409):
                raise self._write_error(response)
            existing = self.session.get(f"{self.url}/developerMetadata/{metadata_id}", timeout=30)
            if not existing.ok:
                raise self._write_error(response)
            # The failed batch may have raced another writer, or the old
            # metadata may remain on a row whose cells were edited or cleared.
            self.load(refresh=True)
            if key in self.pairs:
                return self.mapping.get(key)
        else:
            raise TrackOverridesError(f"Could not allocate a unique review row in {TAB}")
        self.pairs.add(key)

    @staticmethod
    def _write_error(response):
        return TrackOverridesError(f"Recording a miss in {TAB} returned HTTP {response.status_code}")
