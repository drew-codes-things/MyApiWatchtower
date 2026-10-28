import checks.anilist as anilist_check
import checks.discord as discord_check
import checks.lastfm as lastfm_check
import checks.mediasuggest_worker as mediasuggest_worker_check
import checks.plex as plex_check
import checks.scrapers as scrapers_check
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
        "id": "anilist",
        "label": "AniList API",
        "projects": ["YtsPlexRss"],
        "kind": "api",
        "run": anilist_check.run,
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
        "id": "reddit_v4_discord_relay",
        "label": "Discord relay (RedditDownloaderV4)",
        "projects": ["RedditDownloaderV4"],
        "kind": "api",
        "run": discord_check.run_reddit_relay,
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
        "kind": "scraper",
        "run": scrapers_check.run_reddit_rss,
    },
    {
        "id": "atsu_moe",
        "label": "atsu.moe",
        "projects": ["MangaDL"],
        "kind": "scraper",
        "run": scrapers_check.run_atsu,
    },
    {
        "id": "dvdsreleasedates",
        "label": "dvdsreleasedates.com",
        "projects": ["YtsSearch"],
        "kind": "scraper",
        "run": scrapers_check.run_dvdsreleasedates,
    },
]


def get_check(check_id):
    for check in CHECKS:
        if check["id"] == check_id:
            return check
    return None
