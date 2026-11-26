# DJ Set Processor

One Python command scans a configured Google Drive folder for VirtualDJ M3U files. One Render cron job runs it every 15 minutes. Start with a manual Render test of the DJ History workflow, then add Spotify.

## Filenames and workflow state

Read a single tag group directly before `.m3u`. Accept parentheses or square brackets and separate tokens with semicolons. Accept `DH` and `SP` in either order, with each workflow appearing once. Trim surrounding whitespace on each token. Only the exact case-sensitive tokens `DH` and `SP` are runnable. A checkmark means the output succeeded. A red X means it failed and needs manual review.

```text
2026.10.10 01 Atlanta Swing Classic [DH; SP].m3u
2026.10.10 01 Atlanta Swing Classic [DH ✅; SP ❌].m3u
```

Process pending tokens in the listed order. After each attempt, immediately save its status by renaming the file, keeping the other tokens and the original delimiters. Continue with the other pending workflow after saving a failure marker. Skip both completed and failed tokens on subsequent runs. Remove an emoji to request that workflow again. The filename is the only workflow state.

If a status rename fails, stop work on that file and continue with other files. Log whether the output was written and that the pending token may run again if Drive did not save the marker. Any failure makes the run exit nonzero, even if all other work succeeds. Enable email and select **Only failure notifications** for the Render cron job so the user gets an email for every failed run. A run with no pending work succeeds. Keep credentials, private configuration, and playlist contents out of Git and logs.

Spotify's refresh-token `invalid_grant` response marks pending Spotify exports `SP ⛔️` and fails the first run. The blocked tags record the outage. While any remain, a single authorization check determines whether Spotify exports can proceed. If Spotify still returns `invalid_grant`, additional pending exports receive the same marker without another failure alert for the outage. A successful check makes blocked exports eligible in their original processing order. An unexpected check failure leaves Spotify tags in place and fails the run. History imports remain eligible throughout.

## DJ History

- Parse `YYYY.MM.DD`, an optional two-digit sequence immediately after the date, and the event name. Process dates oldest first and parts of the same date and event in numeric sequence. Report duplicate sequence numbers or mixed numbered and unnumbered files in the same group before writing that group.
- Append every play in source order, preserving repeats, to the tab named for the year in the filename. If the tab is missing, create it at the far left and copy the prior year's row 1 values and formatting, including row height and column widths. Use the prior year's row 2 as the template for song-row formatting and formulas. Report a missing prior-year template as a failure.
- Start a new tab's song rows at row 2. Fill title, artist, duration, date, and venue. Store duration as text in `m:ss` format, rounding to whole seconds. Apply native Sheets formulas with adjusted row references and preserve lookups into `Event Details`. Leave Spotify and Competition blank.
- Match title and artist against `Bumper Music`, ignoring capitalization and extra whitespace while keeping version and remix distinctions. Apply a light amber fill across the full imported row, including Spotify and Competition. Apply it once so manual edits to the fill or row persist.

## Spotify

Each attempt creates and populates a new playlist named `YYYY.MM.DD Event`. For example, `2026.10.10 01 Atlanta Swing Classic [DH ✅; SP].m3u` creates `2026.10.10 Atlanta Swing Classic`. Parts and repeat attempts can create separate playlists with the same name. A manual retry after a partial write can create another playlist.

## Verification

Run the offline regression suite before deploying code changes.

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

The integration tests invoke the cron entry point and replace HTTP responses in memory. They cover the tag combinations, ordering, reruns, Sheets write requests, Spotify playlist creation requests, and failure handling without credentials or live writes. Use a live smoke test for changes to credentials, Google workbook templates, external API behavior, or Render configuration and notifications.

Reuse the VirtualDJ parser from `m3u-processor` and the Drive listing and download patterns from Westie Radio. Keep the service-account key in Render Secret Files. Confirm Drive listing, download, and rename access and workbook editing access. Inspect a representative year tab, `Event Details`, and `Bumper Music` for column mappings, formulas, and venue aliases, including `ASC`.

Use a disposable playlist and test workbook to verify ordering, repeated plays, year-tab creation, formulas, full-row highlighting, and filename sync back to the Mac. Test that successful and failed tokens are skipped, that clearing a marker requests only its workflow, and that either workflow can finish while the other is marked failed. Verify that a status rename failure stops work on the affected file and is reported as a run failure.

Trigger a controlled failure on Render and verify that the failure email arrives, including when the other workflow succeeds.
