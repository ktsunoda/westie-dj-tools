"""Authorize Spotify once and save shell-compatible credentials outside the repo."""

import argparse
from getpass import getpass
import os
from pathlib import Path
from secrets import compare_digest, token_urlsafe
from shlex import quote
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

import requests

from spotify_output import SETTINGS, TOKEN_URL

REDIRECT_URI = "http://127.0.0.1:8765/callback"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New credential file outside the repository")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.is_relative_to(Path(__file__).resolve().parent):
        parser.error("Choose a credential file outside the repository")
    if output.exists():
        parser.error("Choose a credential file that does not already exist")

    client_id = input("Spotify client ID: ").strip()
    client_secret = getpass("Spotify client secret: ").strip()
    if not client_id or not client_secret:
        parser.error("Client ID and secret are required")

    state = token_urlsafe(32)
    query = urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT_URI,
        "scope": "playlist-modify-private", "state": state,
    })
    consent_url = "https://accounts.spotify.com/authorize?" + query
    if not webbrowser.open(consent_url):
        print(consent_url)
    redirect = urlsplit(getpass("After approving, paste the URL from the browser address bar: ").strip())
    values = parse_qs(redirect.query)
    if (redirect._replace(query="", fragment="").geturl() != REDIRECT_URI
            or not compare_digest(values.get("state", [""])[0], state)
            or not values.get("code")):
        parser.exit(1, "Spotify authorization failed or the callback did not match.\n")

    try:
        response = requests.post(
            TOKEN_URL, auth=(client_id, client_secret),
            data={"grant_type": "authorization_code", "code": values["code"][0],
                  "redirect_uri": REDIRECT_URI}, timeout=30,
        )
        response.raise_for_status()
        token = response.json()["refresh_token"]
    except (requests.RequestException, ValueError, KeyError):
        parser.exit(1, "Spotify token exchange failed. Check the app credentials and redirect URI.\n")

    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    contents = "".join(
        f"export {name}={quote(value)}\n"
        for name, value in zip(SETTINGS, (client_id, client_secret, token))
    )
    with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        handle.write(contents)
    print(f"Saved Spotify credentials to {output}")


if __name__ == "__main__":
    main()
