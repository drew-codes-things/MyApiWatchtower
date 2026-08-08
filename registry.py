import checks.discord as discord_check
import checks.jikan as jikan_check
import checks.lastfm as lastfm_check
import checks.mediasuggest_worker as mediasuggest_worker_check
import checks.plex as plex_check
import checks.reachability as reachability_check
import checks.spotify as spotify_check
import checks.tmdb as tmdb_check
import checks.yts as yts_check

CHECKS = [
    {
        "id": "tmdb",
        "label": "TMDB API",
        "projects": ["DrewBot", "TmdbBackdropDL"],
        "kind": "api",
        "run": tmdb_check.run,
    },
    {
        "id": "jikan",
        "label": "Jikan API",
        "projects": ["MediaSuggest"],
        "kind": "api",
        "run": jikan_check.run,
    },
    {
        "id": "mediasuggest_worker",
        "label": "MediaSuggest TMDB Worker",
        "projects": ["MediaSuggest"],
        "kind": "api",
        "run": mediasuggest_worker_check.run,
    },
    {
        "id": "spotify_client_credentials",
        "label": "Spotify (Client Credentials)",
        "projects": ["SpotifyInfoExtract"],
        "kind": "api",
        "run": spotify_check.run_client_credentials,
    },
    {
        "id": "spotify_user_oauth",
        "label": "Spotify (User OAuth)",
        "projects": ["SpotifyDashTool", "DrewsMusicBot"],
        "kind": "api",
        "run": spotify_check.run_user_oauth,
    },
    {
        "id": "lastfm",
        "label": "Last.fm API",
        "projects": ["SpotifyDashTool"],
        "kind": "api",
        "run": lastfm_check.run,
    },
    {
        "id": "discord_drewbot",
        "label": "Discord Bot Token (DrewBot)",
        "projects": ["DrewBot"],
        "kind": "api",
        "run": lambda: discord_check.run("DREWBOT_DISCORD_TOKEN"),
    },
    {
        "id": "discord_drewsmusicbot",
        "label": "Discord Bot Token (DrewsMusicBot)",
        "projects": ["DrewsMusicBot"],
        "kind": "api",
        "run": lambda: discord_check.run("DREWSMUSICBOT_DISCORD_TOKEN"),
    },
    {
        "id": "plex",
        "label": "Plex (via plex.tv)",
        "projects": ["PlexDiscordPresence", "PlexLibraryCompare", "YtsPlexRss"],
        "kind": "api",
        "run": plex_check.run,
    },
    {
        "id": "yts",
        "label": "YTS API",
        "projects": ["YtsPlexRss", "YtsSearch"],
        "kind": "api",
        "run": yts_check.run,
    },
    {
        "id": "reddit_rss",
        "label": "Reddit RSS feed",
        "projects": ["RedditMediaDL"],
        "kind": "reachability",
        "run": lambda: reachability_check.run("https://www.reddit.com/r/popular/.rss", "Reddit RSS"),
    },
    {
        "id": "atsu_moe",
        "label": "atsu.moe",
        "projects": ["MangaDL"],
        "kind": "reachability",
        "run": lambda: reachability_check.run("https://atsu.moe/", "atsu.moe"),
    },
    {
        "id": "dvdsreleasedates",
        "label": "dvdsreleasedates.com",
        "projects": ["YtsSearch"],
        "kind": "reachability",
        "run": lambda: reachability_check.run("https://www.dvdsreleasedates.com/", "dvdsreleasedates.com"),
    },
]


def get_check(check_id):
    for check in CHECKS:
        if check["id"] == check_id:
            return check
    return None
