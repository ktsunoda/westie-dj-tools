# Westie DJ Tools

Python tools for West Coast Swing DJs to archive sets, share recent plays, and build contest music playlists.

| Tool | What it does |
| --- | --- |
| [DJ Set Processor](tools/dj-set-processor/README.md) | Reads tagged VirtualDJ `.m3u` exports from Google Drive. Use `DH` to archive plays in Google Sheets, `SP` to create a Spotify playlist for the event, or both. |
| [Westie Radio](tools/westie-radio/README.md) | Adds new plays from daily VirtualDJ exports in Google Drive to one rolling Spotify playlist. It remembers where it left off in each file and caps the playlist at 200 entries. |
| [Contest Playlist Builder](tools/contest-playlist-builder/README.md) | Reads competition entries from the DJ history workbook, rebuilds song lists for six WCS divisions in Google Sheets, and adds newly processed tracks to each division's Spotify playlist. |
| [SwingStation Sync](tools/swingstation-sync/README.md) | Inventories owned Spotify playlists in Google Sheets and publishes approved local social sets to SwingStation. |

The DJ Set Processor can populate the history workbook that the Contest Playlist Builder reads. Westie Radio runs independently for ongoing playback history.

## Spotify Search

All three tools use the [`shared.spotify` package](shared/spotify/) when looking up tracks. Set `SPOTIFY_SEARCH_SPREADSHEET_ID` to the same standalone workbook ID for each tool and give each existing Google service account Editor access. Enable the Google Sheets API in each service account's project.

Name the workbook **Spotify Search** and its tab **Track Overrides**. Use these headers in A1:C1: `Artist`, `Title`, `Spotify Track URL`.

After a Spotify search finds no match, the tool looks up the original artist and title as an exact, case-sensitive pair. The tool uses the header names to find the columns, so you can rearrange them. Paste a Spotify track URL into an existing row to select that track when the pair is next searched. A blank URL marks a search miss. If the tab has a `Status` column, new misses get `⏳` there. Edit that status manually as you review each row. If the tab has a `Last Accessed` column, the tools record the UTC date (YYYY-MM-DD) when they add a row or find its exact pair after a Spotify miss. New review rows appear at the top, and you can sort the full rows as needed. If a shared matcher fix resolves it, verify the pair without an override and delete the entire sheet row. Contest Playlist Builder uses saved row counters, so updating an override does not retry previously skipped rows.

With the workbook ID unset, the tools use their existing search and failure reporting.

## Setup

Start with the linked README for the tool you want to run. Each covers its required accounts, configuration, and test or deployment steps. The repository's [GitHub Actions workflow](.github/workflows/python-tests.yml) runs the offline tests on Python 3.12.

[MIT license](LICENSE)
