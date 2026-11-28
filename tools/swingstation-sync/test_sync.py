import unittest
from datetime import date
from unittest.mock import Mock

import sync


class SyncTests(unittest.TestCase):
    def test_blank_workbook_bootstraps_inventory_and_all_views(self):
        session = Mock()
        session.get.side_effect = [
            Mock(status_code=400, ok=False),
            Mock(ok=True, **{"json.return_value": {"sheets": [{"properties": {
                "sheetId": 0, "title": "Sheet1", "sheetType": "GRID",
                "gridProperties": {"rowCount": 1000, "columnCount": 26},
            }}]}}),
            Mock(ok=True, **{"json.return_value": {}}),
        ]
        self.assertEqual(sync.PlaylistSheet(session, "workbook").read(initialize=True), (True, {}))
        sheet_check = session.get.call_args
        self.assertTrue(sheet_check.args[0].endswith("/values/%27Sheet1%27"))
        self.assertEqual(sheet_check.kwargs["params"], {"valueRenderOption": "FORMULA"})
        session.post.assert_called_once()
        batch = session.post.call_args.kwargs["json"]["requests"]
        self.assertEqual(batch[0]["updateSpreadsheetProperties"]["properties"]["title"], "SwingStation Sync")
        self.assertEqual(batch[1]["updateSheetProperties"]["properties"]["title"], "Spotify Playlists")
        filters = [r["addFilterView"]["filter"] for r in batch if "addFilterView" in r]
        self.assertEqual(filters, [{
            "title": "Needs Action",
            "range": {"sheetId": 0, "startRowIndex": 0, "startColumnIndex": 0,
                      "endColumnIndex": len(sync.HEADERS)},
            "filterSpecs": [{
                "columnIndex": 4,
                "filterCriteria": {"condition": {
                    "type": "CUSTOM_FORMULA",
                    "values": [{"userEnteredValue": '=OR(INDEX($E:$E,ROW())="⏸️",INDEX($E:$E,ROW())="⚠️",INDEX($E:$E,ROW())="❌")'}],
                }},
            }],
        }])
        self.assertEqual(
            [r["addSheet"]["properties"]["title"] for r in batch if "addSheet" in r],
            ["Needs Action", "Pending", "On SwingStation"],
        )
        cells = [r["updateCells"]["rows"] for r in batch if "updateCells" in r]
        for rows in cells:
            self.assertEqual(
                [cell["userEnteredValue"]["stringValue"] for cell in rows[0]["values"]], sync.HEADERS
            )
        self.assertEqual(
            [rows[1]["values"][0]["userEnteredValue"]["formulaValue"] for rows in cells[1:]],
            [
                """=IFNA(FILTER('Spotify Playlists'!A:F,('Spotify Playlists'!E:E="⏸️")+('Spotify Playlists'!E:E="⚠️")+('Spotify Playlists'!E:E="❌")),"No playlists need action")""",
                """=IFNA(FILTER('Spotify Playlists'!A:F,('Spotify Playlists'!E:E="▶️")+('Spotify Playlists'!E:E="⚠️")),"No pending playlists")""",
                """=IFNA(FILTER('Spotify Playlists'!A:F,('Spotify Playlists'!E:E="✅")),"Nothing on SwingStation yet")""",
            ],
        )
        validation = [r["setDataValidation"] for r in batch if "setDataValidation" in r]
        self.assertEqual(len(validation), 1)
        self.assertEqual(
            [option["userEnteredValue"] for option in validation[0]["rule"]["condition"]["values"]],
            ["⏸️", "▶️", "⚠️", "❌", "🚫", "✅"],
        )
        self.assertEqual(validation[0]["range"], {
            "sheetId": 0, "startRowIndex": 1, "startColumnIndex": 4, "endColumnIndex": 5,
        })

    def test_bootstrap_preserves_nonempty_and_multi_tab_workbooks(self):
        grid_tab = {"properties": {"sheetId": 0, "title": "Sheet1", "sheetType": "GRID"}}
        for tabs, values in (([grid_tab, grid_tab], []), ([grid_tab], [["=IF(TRUE,\"\",1)"]])):
            with self.subTest(tabs=tabs, values=values):
                session = Mock()
                session.get.side_effect = [
                    Mock(status_code=400, ok=False),
                    Mock(ok=True, **{"json.return_value": {"sheets": tabs}}),
                    Mock(ok=True, **{"json.return_value": {"values": values}}),
                ]
                with self.assertRaisesRegex(RuntimeError, "requires .*empty"):
                    sync.PlaylistSheet(session, "workbook").read(initialize=True)
                session.post.assert_not_called()

    def test_title_formats_and_invalid_dates(self):
        for title in ("2026.06.16 Event", "2026-06-16 Event", "16.06.2026 Event"):
            with self.subTest(title=title):
                self.assertEqual(sync.parse_title(title), (date(2026, 6, 16), "Event", None))
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
        sheet.insert_at_top.assert_called_once_with(
            [
                ["public", "2026.11.01 Local Social", "url", "Public", sync.APPROVED, ""],
                ["private", "Private", "url", "Private", sync.IGNORED, "Private playlist"],
            ]
        )
        self.assertEqual([item[0] for item in sheet.method_calls], ["read", "write", "insert_at_top"])

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
        sheet.insert_at_top([])
        session.get.assert_not_called()
        session.post.assert_not_called()

        sheet.insert_at_top([["one", "=literal"], ["two", "Second"]])

        self.assertTrue(session.post.call_args.args[0].endswith("/workbook:batchUpdate"))
        insert, *copies, write = session.post.call_args.kwargs["json"]["requests"]
        self.assertEqual(
            [request["copyPaste"]["pasteType"] for request in copies],
            ["PASTE_FORMAT", "PASTE_DATA_VALIDATION", "PASTE_CONDITIONAL_FORMATTING"],
        )
        columns = {"sheetId": 42, "startColumnIndex": 0, "endColumnIndex": len(sync.HEADERS)}
        for request in copies:
            self.assertEqual(
                request["copyPaste"]["source"], {**columns, "startRowIndex": 3, "endRowIndex": 4}
            )
            self.assertEqual(
                request["copyPaste"]["destination"], {**columns, "startRowIndex": 1, "endRowIndex": 3}
            )
        self.assertEqual(write["updateCells"]["fields"], "userEnteredValue")
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
        missing = [sync.WARNING, sync.EVENT_MISSING + "Event"]
        sheet.write.assert_called_once_with(
            [{"range": "'Spotify Playlists'!E2:F2", "values": [missing]}]
        )
        sheet.reset_mock()
        row[4:6] = [sync.WARNING, "Edited explanation"]
        event = {"id": "event-id", "type": "local event", "name": "Event"}
        session.post.return_value.status_code = 409
        session.post.return_value.json.return_value = {"error": {"code": "already_uploaded"}}
        with self.assertLogs(level="INFO") as logs:
            sync.process_queue(sheet, session, "key", playlists, lambda: {"event": [event]})
        self.assertIn("SwingStation reported already_uploaded for url", "\n".join(logs.output))
        self.assertEqual(
            session.post.call_args.kwargs["json"],
            {"spotify_url": "url", "event_id": "event-id", "date": "2026-11-01"},
        )
        sheet.write.assert_called_once_with(
            [{"range": "'Spotify Playlists'!E2:F2", "values": [[sync.DONE, ""]]}]
        )

    def test_error_does_not_retry_even_with_old_missing_event_reason(self):
        sheet, session, events = Mock(), Mock(), Mock()
        row = ["id", "2026.11.01 Event", "url", "Public", sync.ERROR, "Event not found: Event"]
        sheet.read.return_value = True, {"id": (2, row)}
        sync.process_queue(sheet, session, "key", {"id": row[1:4]}, events)
        events.assert_not_called()
        session.post.assert_not_called()
        sheet.write.assert_not_called()

    def test_timed_convention_title_publishes_with_normalized_start_time(self):
        sheet, session = Mock(), Mock()
        row = ["id", "2026.03.21 King Swing 2026 (6:00)", "url", "Public", sync.APPROVED, ""]
        sheet.read.return_value = True, {"id": (2, row)}
        event = {"id": "convention-id", "type": "convention", "name": "King Swing 2026"}
        session.post.return_value.status_code = 201
        with self.assertLogs(level="INFO") as logs:
            sync.process_queue(
                sheet, session, "key", {"id": row[1:4]}, lambda: {"king swing 2026": [event]}
            )
        self.assertIn("SwingStation published a new set for url", "\n".join(logs.output))
        self.assertEqual(session.post.call_args.kwargs["json"], {
            "spotify_url": "url", "event_id": "convention-id", "date": "2026-03-21",
            "start_time": "06:00",
        })
        sheet.write.assert_called_once_with(
            [{"range": "'Spotify Playlists'!E2:F2", "values": [[sync.DONE, ""]]}]
        )

    def test_convention_without_time_needs_action_without_posting(self):
        session = Mock()
        event = {"id": "convention-id", "type": "convention"}
        self.assertEqual(
            sync.publish(session, "key", event, date(2026, 3, 21), "url"),
            (sync.ERROR, "Add a start time to the playlist title, e.g. (23:00)"),
        )
        session.post.assert_not_called()

    def test_title_rejects_invalid_times_and_preserves_event_parentheses(self):
        for title in ("2026.11.01 Event (24:00)", "2026.11.01 Event (6:3)", "2026.11.01 (06:00)"):
            with self.subTest(title=title), self.assertRaises(ValueError):
                sync.parse_title(title)
        self.assertEqual(
            sync.parse_title("2026.11.01 Event (Downtown)"),
            (date(2026, 11, 1), "Event (Downtown)", None),
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
