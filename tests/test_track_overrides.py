import unittest
from unittest.mock import Mock

from spotify_search import TrackOverrides, find_track


ARTIST = "Michael Jackson & Akon"
TITLE = "Hold My Hand (Remix)"
TRACK_ID = "0123456789abcdefghijkl"
URL = f"https://open.spotify.com/track/{TRACK_ID}?si=share"
HEADERS = ["Artist", "Title", "Spotify Track URL"]


def response(payload=None, status=200):
    return Mock(ok=status == 200, status_code=status, json=Mock(return_value=payload))


def sheet(*rows):
    return response({"sheets": [{
        "properties": {"title": "Track Overrides", "sheetId": 0},
        "data": [{"rowData": [
            {"values": [{"formattedValue": cell} for cell in row]}
            for row in [HEADERS, *rows]
        ]}],
    }]})


class TrackOverridesTests(unittest.TestCase):
    def test_override_uses_original_title_without_querying_spotify(self):
        session = Mock()
        session.get.return_value = sheet([ARTIST, TITLE, URL])
        workbook = TrackOverrides(session, "workbook")
        api = Mock()

        uri = find_track(api, ARTIST, "Hold My Hand", "", original_title=TITLE, overrides=workbook.load())

        self.assertEqual(uri, f"spotify:track:{TRACK_ID}")
        api.assert_not_called()
        workbook.record_miss(ARTIST.lower(), TITLE)
        session.get.assert_called_once()
        session.post.assert_not_called()

    def test_unmatched_pair_is_written_once_with_blank_url(self):
        session = Mock()
        session.get.return_value = sheet()
        session.post.return_value = response()
        workbook = TrackOverrides(session, "workbook")

        workbook.record_miss(ARTIST, TITLE)
        workbook.record_miss(ARTIST, TITLE)

        session.post.assert_called_once()
        requests = session.post.call_args.kwargs["json"]["requests"]
        self.assertEqual(requests[1]["updateCells"]["rows"][0]["values"], [
            {"userEnteredValue": {"stringValue": ARTIST}},
            {"userEnteredValue": {"stringValue": TITLE}},
        ])

    def test_concurrent_miss_uses_row_already_created_by_other_tool(self):
        session = Mock()
        session.get.return_value = sheet()
        workbook = TrackOverrides(session, "workbook")
        workbook.load()

        def existing(*args, **kwargs):
            requests = session.post.call_args.kwargs["json"]["requests"]
            return response(requests[2]["createDeveloperMetadata"]["developerMetadata"])

        session.post.return_value = response(status=400)
        session.get.side_effect = existing

        workbook.record_miss(ARTIST, TITLE)
        workbook.record_miss(ARTIST, TITLE)
        session.post.assert_called_once()
