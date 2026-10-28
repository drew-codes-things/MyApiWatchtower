import os

from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

from checks.probe import Probe, StepFailure, StepWarning, get_json, http, non_empty_list, require_fields
from checks.result import not_configured, timed

USER_OAUTH_SCOPES = "playlist-read-private playlist-read-collaborative user-read-private user-library-read"
CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".spotifyoauthcache")
KNOWN_ARTIST_ID = "0TnOYISbd1XYRBk9myaseg"
SPOTIFY_API = "https://api.spotify.com/v1"
MARKET = "GB"


def _client_id_and_secret():
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    return client_id, client_secret


def redirect_uri():
    port = os.environ.get("PORT", "5002").strip() or "5002"
    return f"http://127.0.0.1:{port}/callback"


def _api(token):
    """GET a Spotify Web API path with a bearer token, returning parsed JSON."""
    def get(path, **params):
        return get_json(f"{SPOTIFY_API}{path}", params=params, headers={"Authorization": f"Bearer {token}"})
    return get


def run_client_credentials():
    client_id, client_secret = _client_id_and_secret()
    if not client_id or not client_secret:
        return not_configured("SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET are not set")

    def do_check():
        probe = Probe(client_secret, client_id)
        state = {}

        def token():
            auth_manager = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
            state["token"] = auth_manager.get_access_token(as_dict=False)
            state["get"] = _api(state["token"])
            return "client credentials exchanged for an app token"

        # SpotifyInfoExtract's lookups, in the order it makes them.
        def search():
            body = state["get"]("/search", q="Pitbull", type="artist", limit=1)
            artists = non_empty_list(body, "artists.items")
            require_fields(artists[0], "id", "name")
            return f"found '{artists[0]['name']}'"

        def artist():
            body = state["get"](f"/artists/{KNOWN_ARTIST_ID}")
            require_fields(body, "name", "images")
            dropped = [name for name in ("genres", "followers", "popularity") if name not in body]
            if dropped:
                raise StepWarning(f"'{body['name']}' came back without {', '.join(dropped)} - SpotifyInfoExtract "
                                  "shows N/A for those")
            return f"'{body['name']}'"

        def artist_albums():
            body = state["get"](f"/artists/{KNOWN_ARTIST_ID}/albums", limit=5, include_groups="album,single")
            albums = non_empty_list(body, "items")
            require_fields(albums[0], "id", "name", "release_date", "total_tracks")
            require_fields(body, "next")
            return f"{body.get('total', len(albums))} albums/singles", albums[0]["id"]

        def album(album_id):
            body = state["get"](f"/albums/{album_id}")
            tracks = non_empty_list(body, "tracks.items")
            require_fields(tracks[0], "name", "duration_ms", "track_number")
            return f"'{body['name']}' with {len(tracks)} tracks"

        def top_tracks():
            response = http("GET", f"{SPOTIFY_API}/artists/{KNOWN_ARTIST_ID}/top-tracks", params={"market": MARKET},
                            headers={"Authorization": f"Bearer {state['token']}"}, expect=(200, 403))
            if response.status_code == 403:
                raise StepWarning("HTTP 403 - Spotify no longer gives this app top tracks; SpotifyInfoExtract "
                                  "lists the artist without them")
            tracks = non_empty_list(response.json(), "tracks")
            return f"{len(tracks)} top tracks"

        have_token = probe.step("App token (Client Credentials)", token)
        needs_token = [("an app token", have_token)]
        probe.step("SpotifyInfoExtract search (/search)", search, needs=needs_token)
        probe.step("SpotifyInfoExtract artist (/artists/{id})", artist, needs=needs_token)
        album_id = probe.step("SpotifyInfoExtract albums (/artists/{id}/albums)", artist_albums, needs=needs_token)
        probe.step("SpotifyInfoExtract album (/albums/{id})", lambda: album(album_id), needs=[("an album id", album_id)])
        probe.step("SpotifyInfoExtract top tracks (/artists/{id}/top-tracks)", top_tracks, needs=needs_token)
        return probe.result()

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
        return not_configured("Not connected yet, check the terminal app.py was started from for the authorize link")

    def do_check():
        probe = Probe(client_secret, client_id)
        state = {}

        def refresh():
            oauth_manager = make_oauth_manager()
            token_info = oauth_manager.validate_token(oauth_manager.cache_handler.get_cached_token())
            if not token_info:
                raise StepFailure("the saved refresh token was rejected - log in again from the terminal")
            state["token"] = token_info["access_token"]
            state["get"] = _api(state["token"])
            return "refresh token still valid"

        def me():
            body = state["get"]("/me")
            require_fields(body, "id")
            return f"logged in as '{body.get('display_name') or body['id']}'"

        def playlists():
            # SpotifyDashTool's playlist list. Spotify now reports a playlist's entries under
            # "items" ({href, total}); "tracks" is the old name some responses may still use.
            body = state["get"]("/me/playlists", limit=50)
            playlists_found = [p for p in non_empty_list(body, "items") if p]
            require_fields(playlists_found[0], "id", "name", "owner")

            def entry_count(playlist):
                return (playlist.get("items") or playlist.get("tracks") or {}).get("total")

            if all(entry_count(p) is None for p in playlists_found):
                raise StepFailure("playlists report no entry count under 'items' or 'tracks', so "
                                  "SpotifyDashTool would show every playlist as empty")
            with_entries = next((p for p in playlists_found if entry_count(p)), None)
            if with_entries is None:
                raise StepFailure("every playlist reports 0 entries, so there's nothing to test reading")
            return f"{len(playlists_found)} playlists with entry counts", with_entries["id"]

        def playlist_items(playlist_id):
            # DrewsMusicBot reads playlists through /items, where each entry's track is under "item".
            body = state["get"](f"/playlists/{playlist_id}/items", offset=0, limit=10)
            entries = non_empty_list(body, "items")
            first = next((e for e in entries if e.get("item")), None)
            if first is None:
                raise StepFailure("entries have no 'item' field, so DrewsMusicBot would queue nothing")
            require_fields(first, "item.id", "item.name", "item.artists[0].name")
            require_fields(body, "total")
            return f"{body['total']} entries, first '{first['item']['name']}'", first["item"]["id"]

        def track(track_id):
            # SpotifyDashTool's /api/tracks/:id proxy.
            body = state["get"](f"/tracks/{track_id}")
            require_fields(body, "name", "artists[0].name", "album.name", "duration_ms")
            return f"'{body['name']}'"

        def audio_features(track_id):
            # SpotifyDashTool shows audio features but treats 403/404 as "none", so losing them degrades it.
            response = http("GET", f"{SPOTIFY_API}/audio-features", params={"ids": track_id},
                            headers={"Authorization": f"Bearer {state['token']}"}, expect=(200, 403, 404))
            if response.status_code != 200:
                raise StepWarning(f"HTTP {response.status_code} - Spotify no longer gives this app audio "
                                  "features; SpotifyDashTool shows its playlist stats without them")
            return "available"

        have_token = probe.step("Refresh the saved login", refresh)
        needs_token = [("a user token", have_token)]
        probe.step("Profile (/me)", me, needs=needs_token)
        playlist_id = probe.step("SpotifyDashTool playlists (/me/playlists)", playlists, needs=needs_token)
        track_id = probe.step("DrewsMusicBot playlist read (/playlists/{id}/items)",
                              lambda: playlist_items(playlist_id), needs=[("a playlist", playlist_id)])
        probe.step("SpotifyDashTool track (/tracks/{id})", lambda: track(track_id), needs=[("a track", track_id)])
        probe.step("SpotifyDashTool audio features (/audio-features)", lambda: audio_features(track_id),
                   needs=[("a track", track_id)])
        return probe.result()

    return timed(do_check)
