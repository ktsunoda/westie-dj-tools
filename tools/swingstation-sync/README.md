# SwingStation Sync

Run this standalone job daily. It refreshes owned Spotify playlists in the
**Spotify Playlists** tab of the **SwingStation Sync** workbook, then processes
approved public playlists. The job uses the Spotify playlist ID to update
metadata while preserving approval and ignore decisions. Newly discovered playlists are inserted directly below
the header. New rows copy cell formatting, dropdown validation, and conditional
formatting from the row immediately below them. Playlist values are generated
for each new row.

To set up a new workbook, share a Google spreadsheet with exactly one empty tab
with the service account as Editor. On the first run, the job renames the
workbook and its first tab, sets up the headers, column widths and Status
dropdown, and adds three live views. **Needs Action** shows ⏸️ and ❌,
**Pending** shows ▶️, and **On SwingStation** shows ✅. Edit statuses in
**Spotify Playlists**. The setup only runs on a workbook with one empty tab.

| Status | Meaning |
| --- | --- |
| ⏸️ | Undecided. Default for new public playlists. |
| ▶️ | Approved. The job looks for a date and an existing SwingStation event. |
| ✅ | Published or already uploaded. |
| ❌ | Needs action. Read the Reason column. Missing events are rechecked on each run. For other issues, fix the problem and switch the status back to ▶️. |
| 🚫 | Ignored. New private playlists get this status and the reason Private playlist. You can also set it manually. |

Approved titles start with a date in `YYYY.MM.DD`, `YYYY-MM-DD`, or
`DD.MM.YYYY` format, followed by the event name. Event matching ignores case
and repeated spaces and requires a unique result. The job keeps approved rows
eligible after temporary upload failures.

For a convention social, add the local 24-hour start time in trailing
parentheses, for example `2026.11.07 Sea to Sky (6:00)`. The job sends the full
set date and normalizes the time to `06:00`. A missing or invalid time on an
approved convention needs action. Local socials can omit the time.

## Auto-approval

With auto-approval enabled, a new public playlist starts at ▶️ only when
its title has a valid set date and its full event name uniquely matches a
SwingStation local event. A title with no date prefix starts at 🚫. Date
formats needing review, missing or ambiguous events, and convention matches
start at ⏸️ with a reason. Existing statuses are preserved.

Inventory-only mode uses manual approval defaults and skips event lookup.

## Configuration

| Environment variable | Value |
| --- | --- |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to the dedicated service-account credentials file. Share the workbook with the service account as Editor. |
| `SPOTIFY_PLAYLIST_SPREADSHEET_ID` | Google Sheets workbook ID. |
| `SPOTIFY_CLIENT_ID` | SwingStation Sync Spotify app client ID. |
| `SPOTIFY_REFRESH_TOKEN` | Refresh token obtained using Spotify Authorization Code with PKCE and the `playlist-read-private` scope. |
| `SWINGSTATION_API_KEY` | Publishing key from your SwingStation profile. |
| `AUTO_APPROVE` | Defaults to `false`. Set to `true` to classify newly discovered public playlists, including the initial inventory if the tab is empty. |

Install with `python -m pip install -r tools/swingstation-sync/requirements.txt`.
Set the environment variables and run
`python tools/swingstation-sync/sync.py`. Use a single daily job so runs do not
overlap. A run can publish any approved row.

Use `python tools/swingstation-sync/sync.py --inventory-only` to refresh the
workbook without contacting SwingStation. This mode requires no
`SWINGSTATION_API_KEY`.
