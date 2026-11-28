# Spotify setup

Use a Spotify Developer Dashboard app that the playlist owner's account can authorize. Spotify limits new Development Mode apps to one client ID per developer, and the owner needs Premium. If your account already owns the Westie Radio app and the dashboard prevents another app, the existing app can be authorized separately for DJ Set Processor. Keep the new refresh token in DJ Set Processor's configuration.

1. In the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), create **DJ Set Processor** with **Web API** selected, or open the existing app. Add `http://127.0.0.1:8765/callback` to its redirect URIs. Keep any existing URIs.
2. Authorize the playlist owner's account with the `playlist-modify-private` scope and exchange the authorization code for a refresh token. Follow the [Spotify authorization code guide](https://developer.spotify.com/documentation/web-api/tutorials/code-flow). If you have the `$use-spotify` skill installed, you can use its first-time setup for DJ Set Processor instead.
3. Set `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, and `SPOTIFY_REFRESH_TOKEN` in your shell for local tests or in the cron job's environment on Render. Keep the client secret, callback URL, and refresh token out of chat and Git.

A bare `SP` enables a Spotify attempt when the collector has Spotify configuration. It creates a private playlist from matching tracks, in set order, with repeats. Unmatched plays are skipped and counted in logs. A set with no matching tracks fails without creating a playlist.

Matching prefers the original title and artist credit. When filtered search does not yield a verified match, the collector also tries a plain-text search with the title and artist together. Both searches check Spotify's artist credits and retain version labels such as Radio Edit, Live, and Remix.

If Spotify omits a collaborator, the collector can accept a single recording with the same title and version and the lead artist credited. If multiple distinct recordings qualify for this fallback, the play stays unmatched.
