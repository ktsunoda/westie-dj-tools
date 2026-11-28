"""Common Spotify HTTP requests with response handling left to callers."""

API_URL = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"


def refresh_access_token(post, client_id, client_secret, refresh_token):
    return post(
        TOKEN_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30,
    )


def request_spotify(request, method, path, *, access_token=None, **kwargs):
    if access_token is not None:
        kwargs["headers"] = {"Authorization": f"Bearer {access_token}"}
    return request(method, f"{API_URL}/{path.lstrip('/')}", timeout=30, **kwargs)


def is_invalid_grant(response):
    if response.status_code != 400:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("error") == "invalid_grant"
