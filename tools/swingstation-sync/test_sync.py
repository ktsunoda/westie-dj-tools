import unittest
from datetime import date
from unittest.mock import Mock, patch

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
        sync.refresh_inventory(
            sheet,
            {
                "old": ["After", "url", "Public"],
                "public": ["Unparsed title", "url", "Public"],
                "private": ["Private", "url", "Private"],
            },
            auto_approve=True,
        )
        sheet.write.assert_called_once_with(
            [{"range": "'Spotify Playlists'!B2:D2", "values": [["After", "url", "Public"]]}]
        )
        sheet.append.assert_called_once_with(
            [
                ["public", "Unparsed title", "url", "Public", sync.APPROVED, ""],
                ["private", "Private", "url", "Private", sync.IGNORED, "Private playlist"],
            ]
        )

    def test_missing_event_is_retried_without_reapproval(self):
        sheet, session = Mock(), Mock()
        row = ["id", "2026.11.01 Event", "url", "Public", sync.APPROVED, ""]
        sheet.read.return_value = True, {"id": (2, row)}
        playlists = {"id": row[1:4]}
        with patch.object(sync, "event_index", return_value={}):
            sync.process_queue(sheet, session, "key", playlists)
        missing = [sync.ERROR, sync.EVENT_MISSING + "Event"]
        sheet.write.assert_called_once_with(
            [{"range": "'Spotify Playlists'!E2:F2", "values": [missing]}]
        )
        sheet.reset_mock()
        row[4:6] = missing
        event = {"id": "event-id", "type": "local event", "name": "Event"}
        session.post.return_value.status_code = 409
        session.post.return_value.json.return_value = {"error": {"code": "already_uploaded"}}
        with patch.object(sync, "event_index", return_value={"event": [event]}):
            sync.process_queue(sheet, session, "key", playlists)
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
        sync.process_queue(sheet, session, "key", {"paused": paused[1:4], "approved": approved[1:4]})
        update = sheet.write.call_args.args[0][0]
        self.assertEqual(update["range"], "'Spotify Playlists'!E3:F3")
        self.assertEqual(update["values"][0][0], sync.ERROR)
        self.assertIn("date", update["values"][0][1])
        session.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
