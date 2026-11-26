import unittest
from unittest.mock import Mock

from spotify_search import TrackOverrides


ARTIST = "Michael Jackson & Akon"
TITLE = "Hold My Hand (Remix)"
TRACK_ID = "0123456789abcdefghijkl"
URL = f"https://open.spotify.com/track/{TRACK_ID}?si=share"
HEADERS = ["Artist", "Title", "Spotify Track URL"]


def response(payload=None, status=200):
    return Mock(ok=status == 200, status_code=status, json=Mock(return_value=payload))


def sheet(*rows, headers=HEADERS):
    return response({"sheets": [{
        "properties": {"title": "Track Overrides", "sheetId": 0},
        "data": [{"rowData": [
            {"values": [{"formattedValue": cell} for cell in row]}
            for row in [headers, *rows]
        ]}],
    }]})


class TrackOverridesTests(unittest.TestCase):
    def test_exact_override_and_existing_blank_pair_on_reordered_columns(self):
        session = Mock()
        session.get.return_value = sheet(
            [URL, "Done", TITLE, ARTIST], ["", "Reviewing", "Missing", ARTIST],
            headers=["Spotify Track URL", "Status", "Title", "Artist"],
        )
        session.post.return_value = response()
        workbook = TrackOverrides(session, "workbook")

        self.assertEqual(workbook.resolve_miss(ARTIST, TITLE), f"spotify:track:{TRACK_ID}")
        self.assertIsNone(workbook.resolve_miss(ARTIST, "Missing"))
        session.post.assert_not_called()

        self.assertIsNone(workbook.resolve_miss(ARTIST.lower(), TITLE))
        session.get.assert_called_once()
        updates = session.post.call_args.kwargs["json"]["requests"][1:4]
        cells = {
            update["start"]["columnIndex"]: update["rows"][0]["values"][0]["userEnteredValue"]["stringValue"]
            for item in updates
            for update in [item["updateCells"]]
        }
        self.assertEqual(cells, {3: ARTIST.lower(), 2: TITLE, 1: "⏳"})

    def test_deleted_row_can_be_queued_again_on_next_run(self):
        session = Mock()
        session.get.side_effect = [sheet([ARTIST, TITLE, URL]), sheet()]
        session.post.return_value = response()

        self.assertEqual(TrackOverrides(session, "workbook").resolve_miss(ARTIST, TITLE),
                         f"spotify:track:{TRACK_ID}")
        self.assertIsNone(TrackOverrides(session, "workbook").resolve_miss(ARTIST, TITLE))
        session.post.assert_called_once()

    def test_unmatched_pair_is_written_once_with_blank_url(self):
        session = Mock()
        session.get.return_value = sheet()
        session.post.return_value = response()
        workbook = TrackOverrides(session, "workbook")

        workbook.resolve_miss(ARTIST, TITLE)
        workbook.resolve_miss(ARTIST, TITLE)

        session.post.assert_called_once()
        requests = session.post.call_args.kwargs["json"]["requests"]
        self.assertEqual([item["updateCells"]["rows"][0]["values"][0] for item in requests[1:3]], [
            {"userEnteredValue": {"stringValue": ARTIST}},
            {"userEnteredValue": {"stringValue": TITLE}},
        ])

    def test_concurrent_miss_uses_row_already_created_by_other_tool(self):
        session = Mock()
        session.get.return_value = sheet()
        workbook = TrackOverrides(session, "workbook")
        workbook.load()

        session.post.return_value = response(status=400)
        session.get.side_effect = [response(), sheet([ARTIST, TITLE])]

        workbook.resolve_miss(ARTIST, TITLE)
        workbook.resolve_miss(ARTIST, TITLE)
        session.post.assert_called_once()
