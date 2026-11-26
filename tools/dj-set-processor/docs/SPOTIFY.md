# Spotify setup

Use a Spotify Developer Dashboard app that the playlist owner's account can authorize. Spotify limits new Development Mode apps to one client ID per developer, and the owner needs Premium. If your account already owns the Westie Radio app and the dashboard prevents another app, the existing app can be authorized separately for DJ Set Processor. Keep the new refresh token in DJ Set Processor's configuration.

1. In the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), create **DJ Set Processor** with **Web API** selected, or open the existing app. Add `http://127.0.0.1:8765/callback` to its redirect URIs. Keep any existing URIs.
2. With the project's Python dependencies installed, run this command on your Mac.

   ```sh
   python3 -m spotify_authorize ~/.config/dj-set-processor/spotify.env
   ```

3. Enter the app's client ID and client secret in the terminal when prompted. Approve private playlist access in the browser. The browser may show a connection error at the local callback. Copy the full address bar URL into the waiting terminal prompt. The helper saves credentials with owner-only permissions. Keep the secret, callback URL, and refresh token out of chat and Git.
4. For local tests, load the file with `source ~/.config/dj-set-processor/spotify.env` and run the normal collector command against the test Drive folder and workbook. Enter the same three Spotify settings in Render for deployment.

A bare `SP` enables a Spotify attempt when the collector has Spotify configuration. It creates a private playlist from matching tracks, in set order, with repeats. Unmatched plays are skipped and counted in logs. A set with no matching tracks fails without creating a playlist.

Matching prefers the original title and artist credit. When filtered search does not yield a verified match, the collector also tries a plain-text search with the title and artist together. Both searches check Spotify's artist credits and retain version labels such as Radio Edit, Live, and Remix.

If Spotify omits a collaborator, the collector can accept a single recording with the same title and version and the lead artist credited. If multiple distinct recordings qualify for this fallback, the play stays unmatched.
