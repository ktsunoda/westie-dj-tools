"""Read track overrides and record search misses in a shared Google Sheet."""

import hashlib
import json
import os
import re

TAB = "Track Overrides"
HEADERS = ("Artist", "Title", "Spotify Track URL")
TRACK_URL = re.compile(r"https://open\.spotify\.com/(?:intl-[a-zA-Z-]+/)?track/([A-Za-z0-9]{22})(?:[?#].*)?")


class TrackOverridesError(ValueError):
    """The shared override workbook could not be read or updated."""


def pair_key(artist, title):
    return artist.strip().casefold(), title.strip().casefold()


def override_uri(overrides, artist, title):
    return overrides.get(pair_key(artist, title))


class TrackOverrides:
    def __init__(self, session, spreadsheet_id):
        self.session = session
        self.url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
        self.sheet_id = None
        self.mapping = {}
        self.pairs = set()

    @classmethod
    def from_env(cls, session):
        workbook_id = os.environ.get("SPOTIFY_SEARCH_SPREADSHEET_ID")
        return cls(session, workbook_id) if workbook_id else None

    def load(self):
        if self.sheet_id is not None:
            return self.mapping
        response = self.session.get(self.url, params={
            "ranges": f"'{TAB}'!A:C",
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
        if not rows or tuple(rows[0]) != HEADERS:
            raise TrackOverridesError(f"{TAB} has unexpected headers")
        pairs, mapping = set(), {}
        for index, row in enumerate(rows[1:], 2):
            artist, title, url = (row + ["", "", ""])[:3]
            if not artist.strip() or not title.strip():
                continue
            key = pair_key(artist, title)
            pairs.add(key)
            if url.strip():
                match = TRACK_URL.fullmatch(url.strip())
                if not match:
                    raise TrackOverridesError(f"Invalid Spotify track URL in {TAB} row {index}")
                uri = f"spotify:track:{match[1]}"
                if mapping.setdefault(key, uri) != uri:
                    raise TrackOverridesError(f"Conflicting Spotify track URLs in {TAB} row {index}")
        self.mapping = mapping
        self.pairs = pairs
        self.sheet_id = sheet["properties"]["sheetId"]
        return self.mapping

    def record_miss(self, artist, title):
        self.load()
        key = pair_key(artist, title)
        if not all(key) or key in self.pairs:
            return
        value = json.dumps(key, ensure_ascii=False)
        digest = hashlib.sha256(value.encode()).digest()
        base_id = int.from_bytes(digest[:4], "big")
        row = {"sheetId": self.sheet_id, "dimension": "ROWS", "startIndex": 1, "endIndex": 2}
        for offset in range(5):
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
                {"updateCells": {
                    "start": {"sheetId": self.sheet_id, "rowIndex": 1, "columnIndex": 0},
                    "rows": [{"values": [
                        {"userEnteredValue": {"stringValue": text}} for text in (artist, title)
                    ]}],
                    "fields": "userEnteredValue",
                }},
                {"createDeveloperMetadata": {"developerMetadata": metadata}},
            ]}, timeout=30)
            if response.ok:
                break
            if response.status_code not in (400, 409):
                raise self._write_error(response)
            existing = self.session.get(f"{self.url}/developerMetadata/{metadata_id}", timeout=30)
            if not existing.ok:
                raise self._write_error(response)
            entry = existing.json()
            if entry.get("metadataKey") == metadata["metadataKey"] and entry.get("metadataValue") == value:
                break
        else:
            raise TrackOverridesError(f"Could not allocate a unique review row in {TAB}")
        self.pairs.add(key)

    @staticmethod
    def _write_error(response):
        return TrackOverridesError(f"Recording a miss in {TAB} returned HTTP {response.status_code}")
