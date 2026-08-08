import os

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

from checks.result import fail, not_configured, ok, timed

USER_OAUTH_SCOPES = "playlist-read-private playlist-read-collaborative user-read-private user-library-read"
CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".spotifyoauthcache")
KNOWN_ARTIST_ID = "0TnOYISbd1XYRBk9myaseg"


def _client_id_and_secret():
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    return client_id, client_secret


def redirect_uri():
    override = os.environ.get("SPOTIFY_REDIRECT_URI", "").strip()
    if override:
        return override
    port = os.environ.get("PORT", "5002").strip() or "5002"
    return f"http://127.0.0.1:{port}/spotify/callback"


def run_client_credentials():
    client_id, client_secret = _client_id_and_secret()
    if not client_id or not client_secret:
        return not_configured("SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET are not set")

    def do_check():
        try:
            auth_manager = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
            client = spotipy.Spotify(auth_manager=auth_manager)
            artist = client.artist(KNOWN_ARTIST_ID)
        except Exception as error:
            return fail(f"Client Credentials request failed: {error}")
        if not artist or "name" not in artist:
            return fail("Spotify returned a response with an unexpected shape")
        return ok(f"Client Credentials auth works (fetched '{artist['name']}')")

    return timed(do_check)


def make_oauth_manager():
    client_id, client_secret = _client_id_and_secret()
    if not client_id or not client_secret:
        return None
    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri(),
        scope=USER_OAUTH_SCOPES,
        cache_path=CACHE_PATH,
        open_browser=False,
    )


def is_user_connected():
    oauth_manager = make_oauth_manager()
    if oauth_manager is None:
        return False
    return oauth_manager.cache_handler.get_cached_token() is not None


def run_user_oauth():
    client_id, client_secret = _client_id_and_secret()
    if not client_id or not client_secret:
        return not_configured("SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET are not set")
    if not is_user_connected():
        return not_configured("Not connected yet, use the Connect Spotify button first")

    def do_check():
        try:
            oauth_manager = make_oauth_manager()
            client = spotipy.Spotify(auth_manager=oauth_manager)
            me = client.current_user()
            playlists = client.current_user_playlists(limit=1)
        except Exception as error:
            return fail(f"User OAuth request failed: {error}")
        if not me or "id" not in me:
            return fail("Spotify returned a response with an unexpected shape")
        playlist_count = playlists.get("total", "?") if isinstance(playlists, dict) else "?"
        display_name = me.get("display_name") or me["id"]
        return ok(f"Logged in as '{display_name}', {playlist_count} playlists visible")

    return timed(do_check)
