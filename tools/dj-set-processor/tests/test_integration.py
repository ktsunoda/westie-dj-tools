"""Exercise the cron entry point against in-memory HTTP responses."""

import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from google.auth.credentials import AnonymousCredentials
from requests import Response

from dj_set_processor import main
from google_outputs import HEADERS


class FakeAPIs:
    def __init__(self, files):
        self.files = dict(files)
        self.actions = []
        self.sheet_writes = []
        self.playlists = []
        self.renames = []
        self.row_count = 2
        self.current_file = None
        self.fail_spotify_items = False
        self.spotify_refresh_error = None
        self.spotify_refreshes = 0
        self.fail_rename_for = None
        self.downloads = []
        self.playlist_items = []
        self.list_pages = []
        self.bumper_rows = [["Song", "Artist"], ["First Song", "Test Artist"]]

    def send(self, request, **options):
        uri = urlsplit(request.url)
        path = unquote(uri.path)
        query = parse_qs(uri.query)
        method = request.method
        if method == "GET" and path == "/drive/v3/files":
            self.list_pages.append(query.get("pageToken"))
            assert query["q"] == ["'test-folder' in parents and trashed = false"]
            files = list(self.files.items())
            page = files[1:] if query.get("pageToken") else files[:1]
            payload = {"files": [
                {"id": file_id, "name": name, "mimeType": "audio/x-mpegurl"}
                for file_id, name in page
            ]}
            if len(files) > 1 and not query.get("pageToken"):
                payload["nextPageToken"] = "remaining"
            return self.response(payload)
        if path.startswith("/drive/v3/files/"):
            file_id = path.rsplit("/", 1)[1]
            if method == "GET" and query.get("alt") == ["media"]:
                self.current_file = file_id
                self.downloads.append(file_id)
                title = "Second Song" if file_id == "part2" else "First Song"
                contents = (
                    f"#EXTM3U\n#EXTVDJ:<artist>Test Artist</artist><title>{title}</title>"
                    "<songlength>123</songlength>\ntrack.mp3\n"
                )
                return self.response(text=contents)
            if method == "PATCH":
                if file_id == self.fail_rename_for:
                    return self.response({"error": "rename rejected"}, status=403)
                name = json.loads(request.body)["name"]
                self.files[file_id] = name
                self.renames.append((file_id, name))
                return self.response({"name": name})
        if uri.hostname == "sheets.googleapis.com" and path.startswith("/v4/spreadsheets/test-book"):
            return self.sheets(method, path, query, request.body)
        if uri.hostname in ("accounts.spotify.com", "api.spotify.com"):
            return self.spotify(method, path, request)
        raise AssertionError(f"Unexpected request {method} {path}")

    def sheets(self, method, path, query, body):
        if method == "GET" and path.endswith("/values:batchGet"):
            formulas = ["", "", "", "", "", "=F2", "=G2", "=H2", "=I2", "", "", "=ROW()-1"]
            return self.response({"valueRanges": [
                {"values": [["Existing"]] * self.row_count}, {"values": [HEADERS, formulas]},
                {"values": [["Example"]]}, {"values": self.bumper_rows},
            ]})
        if method == "GET" and "ranges" in query:
            return self.response({"sheets": [{"data": [{
                "columnMetadata": [{"pixelSize": 120}], "rowMetadata": [{"pixelSize": 25}],
            }]}]})
        if method == "GET" and path == "/v4/spreadsheets/test-book":
            return self.response({"sheets": [{"properties": {
                "title": "2026", "sheetId": 1, "gridProperties": {"rowCount": 100},
            }}]})
        if method == "POST" and path.endswith(":batchUpdate"):
            requests = json.loads(body)["requests"]
            self.actions.append((self.current_file, "DH"))
            self.sheet_writes.append(requests)
            self.row_count += len(next(r["updateCells"]["rows"] for r in requests if "updateCells" in r))
            return self.response({})
        raise AssertionError(f"Unexpected Sheets request {method} {path}")

    def spotify(self, method, path, request):
        if method == "POST" and path == "/api/token":
            assert request.headers["Authorization"].startswith("Basic ")
            self.spotify_refreshes += 1
            if self.spotify_refresh_error is not None:
                status, error = self.spotify_refresh_error
                return self.response({"error": error}, status=status)
            return self.response({"access_token": "test-access"})
        assert request.headers["Authorization"] == "Bearer test-access"
        if method == "GET" and path == "/v1/search":
            return self.response({"tracks": {"items": [{
                "name": "First Song", "artists": [{"name": "Test Artist"}], "uri": "spotify:track:test",
            }]}})
        if method == "POST" and path == "/v1/me/playlists":
            self.playlists.append(json.loads(request.body))
            return self.response({"id": "test-playlist"}, status=201)
        if method == "POST" and path == "/v1/playlists/test-playlist/items":
            if self.fail_spotify_items:
                return self.response({"error": "item batch rejected"}, status=500)
            self.actions.append((self.current_file, "SP"))
            self.playlist_items.append(json.loads(request.body)["uris"])
            return self.response({"snapshot_id": "test-snapshot"}, status=201)
        raise AssertionError(f"Unexpected Spotify request {method} {path}")

    @staticmethod
    def response(payload=None, *, text=None, status=200):
        response = Response()
        response.status_code = status
        response.encoding = "utf-8"
        response._content = (text if text is not None else json.dumps(payload)).encode()
        return response


class CronIntegrationTests(unittest.TestCase):
    def run_cron(self, api):
        settings = {
            "GOOGLE_APPLICATION_CREDENTIALS": "/unused/test-key.json",
            "DJ_SET_DRIVE_FOLDER_ID": "test-folder",
            "DJ_SET_SPREADSHEET_ID": "test-book",
            "SPOTIFY_CLIENT_ID": "test-client",
            "SPOTIFY_CLIENT_SECRET": "test-secret",
            "SPOTIFY_REFRESH_TOKEN": "test-refresh",
        }
        with (
            patch.dict(os.environ, settings, clear=True),
            patch("dj_set_processor.Credentials.from_service_account_file",
                  return_value=AnonymousCredentials()),
            patch("requests.Session.send", side_effect=api.send),
        ):
            return main()

    def test_spotify_reauthorization_blocks_once_then_recovers(self):
        api = FakeAPIs({
            "mixed": "2026.10.10 01 Example [SP; DH].m3u",
            "second": "2026.10.10 02 Example (SP).m3u",
        })
        api.spotify_refresh_error = (400, "invalid_grant")
        with self.assertLogs("workflow", level="ERROR") as logs:
            self.assertEqual(self.run_cron(api), 1)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("Reauthorize Spotify", logs.output[0])
        self.assertEqual(api.spotify_refreshes, 1)
        self.assertEqual(api.actions, [("mixed", "DH")])
        self.assertEqual(api.files["mixed"], "2026.10.10 01 Example [SP ⛔️; DH ✅].m3u")
        self.assertEqual(api.files["second"], "2026.10.10 02 Example (SP ⛔️).m3u")
        self.assertEqual(api.downloads, ["mixed"])

        api.files["new"] = "2026.10.11 Example [DH; SP].m3u"
        with self.assertLogs("workflow", level="INFO") as logs:
            self.assertEqual(self.run_cron(api), 0)
        self.assertEqual(logs.output, ["INFO:workflow:Spotify authorization is still blocked"])
        self.assertEqual(api.spotify_refreshes, 2)
        self.assertEqual(api.actions, [("mixed", "DH"), ("new", "DH")])
        self.assertEqual(api.files["new"], "2026.10.11 Example [DH ✅; SP ⛔️].m3u")
        self.assertEqual(api.downloads, ["mixed", "new"])

        api.spotify_refresh_error = None
        self.assertEqual(self.run_cron(api), 0)
        self.assertEqual(api.actions, [
            ("mixed", "DH"), ("new", "DH"), ("mixed", "SP"), ("second", "SP"), ("new", "SP"),
        ])
        self.assertEqual(api.files["mixed"], "2026.10.10 01 Example [SP ✅; DH ✅].m3u")
        self.assertEqual(api.files["second"], "2026.10.10 02 Example (SP ✅).m3u")
        self.assertEqual(api.files["new"], "2026.10.11 Example [DH ✅; SP ✅].m3u")

    def test_other_refresh_error_preserves_blocked_and_pending_tags(self):
        api = FakeAPIs({
            "blocked": "2026.10.10 Example (SP ⛔️).m3u",
            "new": "2026.10.11 Example [SP; DH].m3u",
        })
        api.spotify_refresh_error = (503, "temporarily_unavailable")
        self.assertEqual(self.run_cron(api), 1)
        self.assertEqual(api.spotify_refreshes, 1)
        self.assertEqual(api.actions, [("new", "DH")])
        self.assertEqual(api.files["blocked"], "2026.10.10 Example (SP ⛔️).m3u")
        self.assertEqual(api.files["new"], "2026.10.11 Example [SP; DH ✅].m3u")

    def test_tag_matrix_sequence_and_second_run(self):
        api = FakeAPIs({
            "part2": "2026.10.09 02 Example [DH].m3u",
            "part1": "2026.10.09 01 Example [DH].m3u",
            "untagged": "2026.10.01 Example.m3u",
            "old-s-tag": "2026.10.01 Example [S].m3u",
            "old-p-tag": "2026.10.01 Example [P].m3u",
            "dh": "2026.10.02 Example [DH].m3u",
            "s": "2026.10.03 Example (SP).m3u",
            "dh-s": "2026.10.04 Example [DH; SP].m3u",
            "s-dh": "2026.10.05 Example (SP; DH).m3u",
            "pending-s": "2026.10.06 Example [DH ✅; SP].m3u",
            "pending-dh": "2026.10.07 Example (SP ✅; DH).m3u",
            "finished": "2026.10.08 Example [DH ✅; SP ❌].m3u",
        })
        self.assertEqual(self.run_cron(api), 0)
        self.assertEqual(api.list_pages, [None, ["remaining"]])
        self.assertEqual(api.downloads, ["dh", "s", "dh-s", "s-dh", "pending-s", "pending-dh", "part1", "part2"])
        self.assertEqual(api.actions, [
            ("dh", "DH"), ("s", "SP"), ("dh-s", "DH"), ("dh-s", "SP"),
            ("s-dh", "SP"), ("s-dh", "DH"), ("pending-s", "SP"),
            ("pending-dh", "DH"), ("part1", "DH"), ("part2", "DH"),
        ])
        self.assertEqual(api.renames, [
            ("dh", "2026.10.02 Example [DH ✅].m3u"),
            ("s", "2026.10.03 Example (SP ✅).m3u"),
            ("dh-s", "2026.10.04 Example [DH ✅; SP].m3u"),
            ("dh-s", "2026.10.04 Example [DH ✅; SP ✅].m3u"),
            ("s-dh", "2026.10.05 Example (SP ✅; DH).m3u"),
            ("s-dh", "2026.10.05 Example (SP ✅; DH ✅).m3u"),
            ("pending-s", "2026.10.06 Example [DH ✅; SP ✅].m3u"),
            ("pending-dh", "2026.10.07 Example (SP ✅; DH ✅).m3u"),
            ("part1", "2026.10.09 01 Example [DH ✅].m3u"),
            ("part2", "2026.10.09 02 Example [DH ✅].m3u"),
        ])
        self.assertEqual(api.playlists, [
            {"name": f"2026.10.{day:02d} Example", "public": False, "description": ""}
            for day in (3, 4, 5, 6)
        ])
        self.assertEqual(api.playlist_items, [["spotify:track:test"]] * 4)
        self.assertEqual(self.run_cron(api), 0)
        self.assertEqual((len(api.downloads), len(api.sheet_writes), len(api.playlists)), (8, 6, 4))

    def test_new_year_requests_leftmost_tab_formulas_and_bumper_format(self):
        api = FakeAPIs({"new": "2027.01.01 Example [DH].m3u"})
        self.assertEqual(self.run_cron(api), 0)
        [requests] = api.sheet_writes
        tab = requests[0]["addSheet"]["properties"]
        self.assertEqual((tab["title"], tab["index"]), ("2027", 0))
        copies = [r["copyPaste"] for r in requests if "copyPaste" in r]
        self.assertEqual([r["pasteType"] for r in copies],
                         ["PASTE_NORMAL", "PASTE_FORMAT", "PASTE_FORMULA", "PASTE_FORMULA"])
        self.assertEqual([(r["destination"]["startColumnIndex"], r["destination"]["endColumnIndex"])
                          for r in copies if r["pasteType"] == "PASTE_FORMULA"], [(5, 9), (11, 12)])
        dimensions = [r["updateDimensionProperties"] for r in requests if "updateDimensionProperties" in r]
        self.assertEqual([r["properties"]["pixelSize"] for r in dimensions], [120, 25])
        write = next(r["updateCells"] for r in requests if "updateCells" in r)
        self.assertEqual(write["start"]["rowIndex"], 1)
        values = [c["userEnteredValue"] for c in write["rows"][0]["values"]]
        self.assertEqual(values[:3], [
            {"stringValue": "Test Artist"}, {"stringValue": "First Song"}, {"stringValue": "2:03"},
        ])
        highlight = next(r["repeatCell"] for r in requests if "repeatCell" in r)
        self.assertEqual((highlight["range"]["startColumnIndex"], highlight["range"]["endColumnIndex"]), (0, 12))

    def test_bumper_columns_are_found_by_header(self):
        for headers, entry in (
            (["Artist", "Song"], ["Test Artist", "First Song"]),
            (["Notes", " Artist ", "", "SONG"], ["Note", "Test Artist", "", "First Song"]),
        ):
            with self.subTest(headers=headers):
                api = FakeAPIs({
                    "part1": "2026.10.09 01 Example [DH].m3u",
                    "part2": "2026.10.09 02 Example [DH].m3u",
                })
                api.bumper_rows = [headers, entry, ["Incomplete row"]]
                self.assertEqual(self.run_cron(api), 0)
                matching, other = api.sheet_writes
                [highlight] = [request["repeatCell"] for request in matching if "repeatCell" in request]
                self.assertEqual(highlight["range"]["endColumnIndex"], 12)
                self.assertFalse(any("repeatCell" in request for request in other))

    def test_spotify_write_failure_saves_red_x_and_history_still_runs(self):
        api = FakeAPIs({"mixed": "2026.10.10 Example [SP; DH].m3u"})
        api.fail_spotify_items = True
        self.assertEqual(self.run_cron(api), 1)
        self.assertEqual(api.actions, [("mixed", "DH")])
        self.assertEqual(api.renames, [
            ("mixed", "2026.10.10 Example [SP ❌; DH].m3u"),
            ("mixed", "2026.10.10 Example [SP ❌; DH ✅].m3u"),
        ])
        self.assertEqual(self.run_cron(api), 0)
        self.assertEqual((len(api.playlists), len(api.sheet_writes)), (1, 1))

    def test_missing_year_template_saves_red_x_and_spotify_still_runs(self):
        api = FakeAPIs({"mixed": "2099.10.10 Example [DH; SP].m3u"})
        self.assertEqual(self.run_cron(api), 1)
        self.assertEqual(api.actions, [("mixed", "SP")])
        self.assertEqual(api.renames, [
            ("mixed", "2099.10.10 Example [DH ❌; SP].m3u"),
            ("mixed", "2099.10.10 Example [DH ❌; SP ✅].m3u"),
        ])
        self.assertEqual(self.run_cron(api), 0)
        self.assertEqual((len(api.playlists), len(api.sheet_writes)), (1, 0))

    def test_rename_failure_stops_file_but_other_files_continue(self):
        api = FakeAPIs({
            "stuck": "2026.10.10 Example [DH; SP].m3u",
            "next": "2026.10.11 Example [DH].m3u",
        })
        api.fail_rename_for = "stuck"
        self.assertEqual(self.run_cron(api), 1)
        self.assertEqual(api.actions, [("stuck", "DH"), ("next", "DH")])
        self.assertEqual(api.files["stuck"], "2026.10.10 Example [DH; SP].m3u")
        self.assertEqual(api.files["next"], "2026.10.11 Example [DH ✅].m3u")
