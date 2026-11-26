# DJ Set Processor

Turn VirtualDJ history files in Google Drive into a play history in Google Sheets, a Spotify playlist, or both. A filename tag tells the processor what to do and records the result. Run it locally or on a schedule.

## Tag a set

Use a UTF-8 VirtualDJ `.m3u` export with `#EXTVDJ` metadata. Put it directly in the configured Drive folder. A filename starts with a date, followed by an optional two-digit set part, the event name, and a tag group immediately before `.m3u`.

```text
2026.10.10 01 Atlanta Swing Classic [DH; SP].m3u
2026.10.10 02 Atlanta Swing Classic [DH].m3u
2026.10.12 Sea to Sky (SP).m3u
```

| Tag | Result |
| --- | --- |
| `DH` | Append plays to the tab for the filename's year, preserving order and repeats. |
| `SP` | Create a Spotify playlist named `YYYY.MM.DD Event` from matched plays. |
| `DH; SP` or `SP; DH` | Run both in the listed order. |

Files with no pending tags are skipped. The processor handles older dates first and sorts parts of the same event and date numerically. Tag all parts before a run if their relative order matters.

After an attempt, the processor renames the file with `✅` for success or `❌` for failure. For example, `[DH ✅; SP ❌]` means Sheets succeeded and Spotify needs review. Remove just the relevant emoji to request that workflow again. A retry can append the same plays or create another Spotify playlist with the same name. If the output succeeded but the Drive rename failed, check the destination before retrying. Spotify may skip unmatched tracks and reports the count in its logs.

`SP ⛔️` means Spotify rejected the refresh token. The first detection fails the run so Render can send its usual failure email. As long as a blocked tag exists, each run checks authorization once before processing exports and stays quiet if Spotify still rejects the token. Update the Spotify refresh token in Render and leave the blocked tags in place. The next scheduled run with working authorization retries them automatically. History imports can continue during the outage.

## Set up your accounts

1. Create a Google Cloud service account and enable the **Google Drive API** and **Google Sheets API** in its project. Save its JSON key outside this checkout. [Google service-account setup](https://developers.google.com/workspace/guides/create-credentials#create_a_service_account)
2. Create a test Drive folder and a test Sheets workbook. Share both with the service account as **Editor** so it can download and rename files and update Sheets. Keep them restricted to the accounts that need access.
3. Prepare the workbook using the layout below. If you want Spotify exports, authorize the app using the [Spotify setup guide](docs/SPOTIFY.md).

### Workbook layout

Create a tab named for the first year you plan to import, such as `2026`. Use these exact column headers in row 1, starting at A1.

| A | B | C | D | E | F | G | H | I | J | K | L |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Artist | Song | Duration | Date | Venue | Event Type | City | State/Province | Country/Region | Competition | Spotify | Row # |

Set up row 2 as the formatting and formula template. Format C as plain text and D as a date. Put these formulas in row 2, using your spreadsheet locale's argument separators.

```text
F2  =IFNA(VLOOKUP($E2,'Event Details'!$B$2:$F,2,FALSE),"")
G2  =IFNA(VLOOKUP($E2,'Event Details'!$B$2:$F,3,FALSE),"")
H2  =IFNA(VLOOKUP($E2,'Event Details'!$B$2:$F,4,FALSE),"")
I2  =IFNA(VLOOKUP($E2,'Event Details'!$B$2:$F,5,FALSE),"")
L2  =ROW()-1
```

- **Event Details** uses columns A–F for First Seen, Name, Type, City, State/Province, and Country/Region. Use event names from your filenames in column B so the formulas can resolve them.
- **Bumper Music** needs one `Song` and one `Artist` header in row 1. They can be in either order or in separate columns. Matching plays get a light amber row fill.
- If a year tab is missing, the app creates it at the far left and uses the immediately preceding year as the template. A missing prior-year tab is a failure.

## Run locally

Use Python 3.12, the version used in CI. Clone the repository and set up a virtual environment.

```sh
git clone https://github.com/ktsunoda/westie-dj-tools.git
cd westie-dj-tools/tools/dj-set-processor
python -m venv .venv
source .venv/bin/activate
export PYTHONPATH="$(cd ../.. && pwd)"
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Set the following in your shell or deployment environment. Use the ID segments from your own Google URLs.

| Variable | Value |
| --- | --- |
| `GOOGLE_APPLICATION_CREDENTIALS` | Absolute path to the service-account JSON key. |
| `DJ_SET_DRIVE_FOLDER_ID` | The part after `/folders/` in the Drive folder URL. |
| `DJ_SET_SPREADSHEET_ID` | The part between `/d/` and `/edit` in the spreadsheet URL. |
| `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REFRESH_TOKEN` | Set all three for Spotify. Omit all three for a Sheets-only setup. |

Run `python -m dj_set_processor` with the test settings first. This is a write operation. Use a disposable tagged file and confirm the output, the Drive marker, and that a second run skips it. For a minimal Sheets test, upload a file named `2026.10.10 Example [DH].m3u` with the following contents. The media path is a placeholder.

```m3u
#EXTM3U
#EXTVDJ:<artist>Test Artist</artist><title>Test Song</title><songlength>123</songlength>
test.mp3
```

## Schedule it on Render

Create a **Python Cron Job** from your repository. Start with test resource IDs and move to production once the manual test passes.

| Setting | Value |
| --- | --- |
| Root directory | Repository root |
| Build command | `pip install -r tools/dj-set-processor/requirements.txt` |
| Run command | `PYTHONPATH=. python tools/dj-set-processor/dj_set_processor.py` |
| Schedule | `*/15 * * * *` for every 15 minutes |
| Google key | Store it as a Render Secret File named `google-service-account.json`. Set `GOOGLE_APPLICATION_CREDENTIALS=/etc/secrets/google-service-account.json`. |

Set the other environment variables on the job. Enable failure email notifications and run a controlled failure on the test resources to verify delivery. Use **Trigger Run** only when the job is idle, since triggering it during an active run cancels that run. [Render cron jobs](https://render.com/docs/cronjobs) · [Secrets](https://render.com/docs/configure-environment-variables#secret-files) · [Notifications](https://render.com/docs/notifications)

## Hand this to your agent

Replace the placeholders with your own test links or let your agent help you create the resources.

> Help me self-host DJ Set Processor using this README and the linked Spotify setup guide. Start with my test Drive folder at `<folder URL>` and test workbook at `<workbook URL>`. I want `<Sheets only or Sheets and Spotify>`.
>
> Check the workbook headers, formulas, and service-account permissions. Run the offline tests, then use a disposable file to verify the output, the Drive status marker, and that the next run skips it. If I ask for Render, check the plan and cost with me before creating a paid job. Verify the saved resource IDs and live build before testing the cron.
>
> Ask me to enter secrets or approve OAuth in the relevant terminal or dashboard. Keep keys and tokens out of chat, source control, and logs. Use only the test resources until I supply production targets and approve the switch. Report what passed and what is still unverified.

[Design and verification details](docs/DESIGN.md) · [MIT license](LICENSE)
