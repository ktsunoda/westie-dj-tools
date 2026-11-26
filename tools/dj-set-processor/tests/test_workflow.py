import unittest
from unittest.mock import Mock, call

from workflow import parse_filename, run_workflows


class WorkflowTests(unittest.TestCase):
    def test_playlist_name_omits_sequence_and_tags(self):
        playlist = parse_filename("id", "2026.10.10 01 Atlanta Swing Classic [DH ✅; SP].m3u")
        self.assertEqual(playlist.spotify_name, "2026.10.10 Atlanta Swing Classic")

    def test_failed_output_is_saved_before_other_output_runs(self):
        rename = Mock()
        history = Mock(side_effect=ValueError)
        spotify = Mock()
        result = run_workflows(
            [("id", "2026.10.10 Event [DH; SP].m3u")],
            Mock(return_value="track.mp3\ntrack.mp3"), rename,
            {"DH": history, "SP": spotify},
        )
        self.assertEqual(result, 1)
        self.assertEqual(rename.call_args_list, [
            call("id", "2026.10.10 Event [DH ❌; SP].m3u"),
            call("id", "2026.10.10 Event [DH ❌; SP ✅].m3u"),
        ])
        self.assertEqual(len(spotify.call_args.args[1]), 2)

    def test_status_rename_failure_stops_file(self):
        spotify = Mock()
        result = run_workflows(
            [("id", "2026.10.10 Event (DH; SP).m3u")],
            Mock(return_value="track.mp3"), Mock(side_effect=OSError),
            {"DH": Mock(), "SP": spotify},
        )
        self.assertEqual(result, 1)
        spotify.assert_not_called()

    def test_only_pending_tokens_run_in_part_order(self):
        history = Mock()
        spotify = Mock()
        download = Mock(return_value="track.mp3")
        result = run_workflows(
            [
                ("two", "2026.10.10 02 Event (SP ❌; DH).m3u"),
                ("one", "2026.10.10 01 Event (DH).m3u"),
                ("done", "2026.10.12 Event [DH ✅; SP ❌].m3u"),
            ],
            download, Mock(), {"DH": history, "SP": spotify},
        )
        self.assertEqual(result, 0)
        self.assertEqual(download.call_args_list, [call("one"), call("two")])
        spotify.assert_not_called()

    def test_empty_playlist_marks_pending_work_failed(self):
        rename = Mock()
        history = Mock()
        result = run_workflows(
            [("id", "2026.10.10 Event (DH).m3u")],
            Mock(return_value="#EXTM3U"), rename, {"DH": history},
        )
        self.assertEqual(result, 1)
        rename.assert_called_once_with("id", "2026.10.10 Event (DH ❌).m3u")
        history.assert_not_called()
