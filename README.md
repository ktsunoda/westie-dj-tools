# Westie DJ Tools

Python tools for West Coast Swing DJs to archive sets, share recent plays, and build contest music playlists.

| Tool | What it does |
| --- | --- |
| [DJ Set Processor](tools/dj-set-processor/README.md) | Reads tagged VirtualDJ `.m3u` exports from Google Drive. Use `DH` to archive plays in Google Sheets, `SP` to create a Spotify playlist for the event, or both. |
| [Westie Radio](tools/westie-radio/README.md) | Adds new plays from daily VirtualDJ exports in Google Drive to one rolling Spotify playlist. It remembers where it left off in each file and caps the playlist at 200 entries. |
| [Contest Playlist Builder](tools/contest-playlist-builder/README.md) | Reads competition entries from the DJ history workbook, rebuilds song lists for six WCS divisions in Google Sheets, and adds newly processed tracks to each division's Spotify playlist. |

The DJ Set Processor can populate the history workbook that the Contest Playlist Builder reads. Westie Radio runs independently for ongoing playback history.

## Shared Spotify matching

All three tools use the [shared Spotify matcher](shared/spotify_matching.py) when looking up tracks. It checks song titles, artist credits, and recording versions so that a remix or edit can be distinguished from the original. Each tool handles unmatched tracks and updates its own destinations.

## Setup

Start with the linked README for the tool you want to run. Each covers its required accounts, configuration, and test or deployment steps. The repository's [GitHub Actions workflow](.github/workflows/python-tests.yml) runs the offline tests on Python 3.12.

[MIT license](LICENSE)
