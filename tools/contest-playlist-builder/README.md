# Contest Playlist Builder

This daily Python job ports Westie Contest Music Tools from Google Apps Script. It reads year tabs from 2012 onward, in ascending order, and rebuilds the six division lists in the existing output workbook. Spotify searches process rows after each division's saved physical sheet row in `Spotify Sync State`. A search with no result is logged to `Failed Searches`, whose timestamp is stored as UTC ISO 8601 text. An API error stops the run. Division checkpoints are saved after that division's work finishes.

The port keeps the original case-insensitive division substring matching, case-sensitive artist/title deduplication. Playlist targets are configured per environment. The shared Spotify matcher checks title, artist credits, and recording version. Use `PYTHONPATH=. python tools/contest-playlist-builder/sync.py --summary` for the separate `Summary` helper.

## Playlist targets

Set `SPOTIFY_PLAYLIST_IDS` to a JSON object containing the six division names and their distinct, bare Spotify playlist IDs. Use test playlist IDs for local write tests and production IDs in Render. Copy this shape and replace every placeholder.

```json
{
  "Newcomer": "<newcomer-playlist-id>",
  "Novice": "<novice-playlist-id>",
  "Intermediate": "<intermediate-playlist-id>",
  "Advanced": "<advanced-playlist-id>",
  "All Star": "<all-star-playlist-id>",
  "Champions": "<champions-playlist-id>"
}
```

`--check` and `--summary` do not require playlist targets.

## Cutover

1. Set up a Google service account with the Sheets API enabled. Give it Viewer access to the DJ history workbook and Editor access to the existing output workbook. Set `SOURCE_SHEET_ID` to the DJ history workbook ID, set `OUTPUT_SHEET_ID` to the existing output workbook ID and point `GOOGLE_APPLICATION_CREDENTIALS` at the service account JSON.
2. From the repository root, install `tools/contest-playlist-builder/requirements.txt` and run `PYTHONPATH=. python tools/contest-playlist-builder/sync.py --check`. This compares the rebuilt prefix through each saved row with the existing division tab and reports the pending counts. It makes no writes and requires no Spotify credentials. Resolve any reported discrepancy before cutover and keep all six saved counters.
3. Obtain a fresh Spotify refresh token for the app using the account permitted to update the six existing playlists. The authorization scopes are `playlist-modify-public playlist-modify-private`. Set `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, and `SPOTIFY_REFRESH_TOKEN` in Render. Keep credentials out of Git.
4. Disable the existing Apps Script refresh and Spotify sync triggers before enabling the Render job so only one job writes to the division tabs and counters. Connect the repo using `render.yaml` and upload the Google JSON as the Render secret file `google-drive.json`. The Blueprint runs daily at noon UTC.
5. Verify the first Render run in its logs and compare the saved rows and playlist additions against the preflight counts. Review a failure after a playlist append before triggering another run.

## Legacy behavior observed in Code.gs

- Division and Summary refreshes overwrite the new result range in place. If a rebuilt list shrinks, cells beyond the new range remain. The port keeps those cells and checks the saved prefix before writing.
- Spotify writes in batches of up to 100 and advances the counter after the entire division. An interrupted run can leave a successful playlist append ahead of the saved counter. The same gap exists if Spotify accepts a write and the Sheets checkpoint write fails. A retry can append those tracks again.
- The November 24, 2026 failure occurred when Apps Script attempted to obtain a Spotify token. The port reports token-refresh and API authorization failures with the operation and HTTP status. It uses Spotify's current playlist items endpoint.

Run the offline checks with `python -m unittest discover -s tools/contest-playlist-builder -v`.
