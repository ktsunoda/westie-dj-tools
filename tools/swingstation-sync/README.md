# SwingStation Sync

Run this standalone job daily. It refreshes owned Spotify playlists in the
**Spotify Playlists** tab of the **SwingStation Sync** workbook, then processes
approved public playlists. The tab can start empty. The job adds its header row
and uses the Spotify playlist ID to update metadata while preserving approval
and ignore decisions. Newly discovered playlists are inserted directly below
the header.

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

Local socials publish with the full date. Convention socials require
verification of the month/year submission against the SwingStation API before
the job can publish them. Until then, the job marks convention rows ❌ and
explains why.

## Configuration

| Environment variable | Value |
| --- | --- |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to the dedicated service-account credentials file. Share the workbook with the service account as Editor. |
| `SPOTIFY_PLAYLIST_SPREADSHEET_ID` | Google Sheets workbook ID. |
| `SPOTIFY_CLIENT_ID` | SwingStation Sync Spotify app client ID. |
| `SPOTIFY_REFRESH_TOKEN` | Refresh token obtained using Spotify Authorization Code with PKCE and the `playlist-read-private` scope. |
| `SWINGSTATION_API_KEY` | Publishing key from your SwingStation profile. |
| `AUTO_APPROVE` | Defaults to `false`. Set to `true` to start newly discovered public playlists at ▶️. This also applies to the initial inventory if the tab is empty. |

Install with `python -m pip install -r tools/swingstation-sync/requirements.txt`.
Set the environment variables and run
`python tools/swingstation-sync/sync.py`. Use a single daily job so runs do not
overlap. A run can publish any approved row.

Use `python tools/swingstation-sync/sync.py --inventory-only` to refresh the
workbook without contacting SwingStation. This mode requires no
`SWINGSTATION_API_KEY`.
