"""Run the tagged DJ Set Processor workflows against a configured Drive folder and workbook."""

import logging
import os

from contextlib import ExitStack

from requests import Session
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from google_outputs import DriveFolder, HistorySheet
from spotify_output import SETTINGS, SpotifyPlaylist
from workflow import run_workflows


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        scopes = ["https://www.googleapis.com/auth/drive",
                  "https://www.googleapis.com/auth/spreadsheets"]
        credentials = Credentials.from_service_account_file(
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"], scopes=scopes
        )
        with ExitStack() as stack:
            session = stack.enter_context(AuthorizedSession(credentials, refresh_timeout=15))
            drive = DriveFolder(session, os.environ["DJ_SET_DRIVE_FOLDER_ID"])
            history = HistorySheet(session, os.environ["DJ_SET_SPREADSHEET_ID"])
            outputs = {"DH": history}
            if any(os.environ.get(name) for name in SETTINGS):
                outputs["SP"] = SpotifyPlaylist(stack.enter_context(Session()))
            spotify = outputs.get("SP")
            return run_workflows(
                list(drive.files()), drive.download, drive.rename, outputs,
                check_spotify_auth=spotify.access_token if spotify is not None else None,
            )
    except Exception as error:
        logging.error("Job setup or Drive listing failed (%s)", type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
