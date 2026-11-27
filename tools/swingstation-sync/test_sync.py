import unittest
from datetime import date
from unittest.mock import Mock

import sync


class SyncTests(unittest.TestCase):
    def test_title_formats_and_invalid_dates(self):
        for title in ("2026.06.16 Event", "2026-06-16 Event", "16.06.2026 Event"):
            with self.subTest(title=title):
                self.assertEqual(sync.parse_title(title), (date(2026, 6, 16), "Event"))
        for title in ("2026.02.30 Event", "2026-06-16", "2026-06-16 - 2026-06-17 Event"):
            with self.subTest(title=title), self.assertRaises(ValueError):
                sync.parse_title(title)

    def test_inventory_preserves_existing_decisions_and_checks_privacy_first(self):
        sheet = Mock()
        sheet.read.return_value = (
            True,
            {"old": (2, ["old", "Before", "url", "Public", sync.IGNORED, "My choice"])},
        )
        events = Mock(return_value={"local social": [{"type": "local event"}]})
        sync.refresh_inventory(
            sheet,
            {
                "old": ["After", "url", "Public"],
                "public": ["2026.11.01 Local Social", "url", "Public"],
                "private": ["Private", "url", "Private"],
            },
            get_auto_approval_events=events,
        )
        sheet.write.assert_called_once_with(
            [{"range": "'Spotify Playlists'!B2:D2", "values": [["After", "url", "Public"]]}]
        )
        sheet.insert_at_top_and_center_status.assert_called_once_with(
            [
                ["public", "2026.11.01 Local Social", "url", "Public", sync.APPROVED, ""],
                ["private", "Private", "url", "Private", sync.IGNORED, "Private playlist"],
            ]
        )
        self.assertEqual([item[0] for item in sheet.method_calls], ["read", "write", "insert_at_top_and_center_status"])

    def test_auto_approval_requires_a_full_date_and_unique_local_event(self):
        events = {
            "local social": [{"type": "local event"}],
            "convention": [{"type": "convention"}],
            "ambiguous": [{"type": "local event"}, {"type": "local event"}],
        }
        cases = [
            ("Road Trip", sync.IGNORED, "No set date"),
            ("2026.02.30 Local Social", sync.PAUSED, "Check date"),
            ("06/07/2026 Local Social", sync.PAUSED, "Check date"),
            ("2026-11-01 Local Social Practice", sync.PAUSED, "Event not found"),
            ("2026-11-01 Ambiguous", sync.PAUSED, "Multiple"),
            ("2026-11-01 Convention", sync.PAUSED, "Event type is not eligible"),
            ("2026-11-01 LOCAL  Social", sync.APPROVED, ""),
        ]
        for title, expected_status, expected_reason in cases:
            with self.subTest(title=title):
                status, reason = sync.initial_status(title, "Public", lambda: events)
                self.assertEqual(status, expected_status)
                self.assertTrue(reason.startswith(expected_reason))
        self.assertEqual(sync.initial_status("Road Trip", "Public", None), (sync.PAUSED, ""))
        lookup = Mock()
        self.assertEqual(
            sync.initial_status("2026-11-01 Local Social", "Private", lookup),
            (sync.IGNORED, "Private playlist"),
        )
        lookup.assert_not_called()

    def test_new_playlists_are_inserted_below_header_in_one_batch(self):
        session = Mock()
        session.get.return_value.json.return_value = {"sheets": [{"properties": {"sheetId": 42}}]}
        sheet = sync.PlaylistSheet(session, "workbook")
        sheet.insert_at_top_and_center_status([])
        alignment_only = session.post.call_args.kwargs["json"]["requests"]
        self.assertEqual(len(alignment_only), 1)

        sheet.insert_at_top_and_center_status([["one", "=literal"], ["two", "Second"]])

        self.assertTrue(session.post.call_args.args[0].endswith("/workbook:batchUpdate"))
        insert, write, unbold, align = session.post.call_args.kwargs["json"]["requests"]
        self.assertEqual(unbold["repeatCell"], {
            "range": {
                "sheetId": 42,
                "startRowIndex": 1,
                "endRowIndex": 3,
                "startColumnIndex": 0,
                "endColumnIndex": len(sync.HEADERS),
            },
            "cell": {"userEnteredFormat": {"textFormat": {"bold": False}}},
            "fields": "userEnteredFormat.textFormat.bold",
        })
        self.assertEqual(alignment_only, [align])
        self.assertEqual(align["repeatCell"], {
            "range": {"sheetId": 42, "startRowIndex": 1, "startColumnIndex": 4, "endColumnIndex": 5},
            "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat.horizontalAlignment",
        })
        self.assertEqual(
            insert["insertDimension"]["range"],
            {"sheetId": 42, "dimension": "ROWS", "startIndex": 1, "endIndex": 3},
        )
        self.assertEqual(write["updateCells"]["start"], {"sheetId": 42, "rowIndex": 1, "columnIndex": 0})
        self.assertEqual(
            write["updateCells"]["rows"][0]["values"][1],
            {"userEnteredValue": {"stringValue": "=literal"}},
        )

    def test_missing_event_is_retried_without_reapproval(self):
        sheet, session = Mock(), Mock()
        row = ["id", "2026.11.01 Event", "url", "Public", sync.APPROVED, ""]
        sheet.read.return_value = True, {"id": (2, row)}
        playlists = {"id": row[1:4]}
        sync.process_queue(sheet, session, "key", playlists, lambda: {})
        missing = [sync.ERROR, sync.EVENT_MISSING + "Event"]
        sheet.write.assert_called_once_with(
            [{"range": "'Spotify Playlists'!E2:F2", "values": [missing]}]
        )
        sheet.reset_mock()
        row[4:6] = missing
        event = {"id": "event-id", "type": "local event", "name": "Event"}
        session.post.return_value.status_code = 409
        session.post.return_value.json.return_value = {"error": {"code": "already_uploaded"}}
        sync.process_queue(sheet, session, "key", playlists, lambda: {"event": [event]})
        self.assertEqual(
            session.post.call_args.kwargs["json"],
            {"spotify_url": "url", "event_id": "event-id", "date": "2026-11-01"},
        )
        sheet.write.assert_called_once_with(
            [{"range": "'Spotify Playlists'!E2:F2", "values": [[sync.DONE, ""]]}]
        )

    def test_only_approved_bad_title_gets_an_error(self):
        sheet, session = Mock(), Mock()
        paused = ["paused", "bad title", "url", "Public", sync.PAUSED, ""]
        approved = ["approved", "bad title", "url", "Public", sync.APPROVED, ""]
        sheet.read.return_value = True, {"paused": (2, paused), "approved": (3, approved)}
        events = Mock()
        sync.process_queue(
            sheet, session, "key", {"paused": paused[1:4], "approved": approved[1:4]}, events
        )
        events.assert_not_called()
        update = sheet.write.call_args.args[0][0]
        self.assertEqual(update["range"], "'Spotify Playlists'!E3:F3")
        self.assertEqual(update["values"][0][0], sync.ERROR)
        self.assertIn("date", update["values"][0][1])
        session.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
