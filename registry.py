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
        "credentials": [
            {"env": "TMDB_API_KEY", "label": "API key", "secret": True},
            {"env": "TMDB_API_URL", "label": "API link", "secret": False, "url": True, "default": tmdb_check.TMDB_BASE},
        ],
    },
    {
        "id": "anilist",
        "label": "AniList API",
        "projects": ["YtsPlexRss"],
        "kind": "api",
        "run": anilist_check.run,
        "credentials": [
            {"env": "ANILIST_API_URL", "label": "API link", "secret": False, "url": True, "default": anilist_check.ANILIST_URL},
        ],
    },
    {
        "id": "mediasuggest_worker",
        "label": "MediaSuggest TMDB Worker",
        "projects": ["MediaSuggest"],
        "kind": "api",
        "run": mediasuggest_worker_check.run,
        "credentials": [
            {"env": "MEDIASUGGEST_WORKER_URL", "label": "API link", "secret": False, "url": True, "default": mediasuggest_worker_check.WORKER_URL},
        ],
    },
    {
        "id": "spotify_client_credentials",
        "label": "Spotify (Client Credentials)",
        "projects": ["SpotifyInfoExtract"],
        "kind": "api",
        "run": spotify_check.run_client_credentials,
        "credentials": [
            {"env": "SPOTIFY_CLIENT_ID", "label": "Client ID", "secret": True},
            {"env": "SPOTIFY_CLIENT_SECRET", "label": "Client secret", "secret": True},
            {"env": "SPOTIFY_API_URL", "label": "API link", "secret": False, "url": True, "default": spotify_check.SPOTIFY_API},
        ],
    },
    {
        "id": "spotify_user_oauth",
        "label": "Spotify (User OAuth)",
        "projects": ["SpotifyDashTool", "DrewsMusicBot"],
        "kind": "api",
        "run": spotify_check.run_user_oauth,
        "credentials": [
            {"env": "SPOTIFY_CLIENT_ID", "label": "Client ID", "secret": True},
            {"env": "SPOTIFY_CLIENT_SECRET", "label": "Client secret", "secret": True},
            {"env": "SPOTIFY_API_URL", "label": "API link", "secret": False, "url": True, "default": spotify_check.SPOTIFY_API},
        ],
    },
    {
        "id": "lastfm",
        "label": "Last.fm API",
        "projects": ["SpotifyDashTool"],
        "kind": "api",
        "run": lastfm_check.run,
        "credentials": [
            {"env": "LASTFM_API_KEY", "label": "API key", "secret": True},
            {"env": "LASTFM_API_URL", "label": "API link", "secret": False, "url": True, "default": lastfm_check.LASTFM_API_URL},
        ],
    },
    {
        "id": "discord_drewbot",
        "label": "Discord Bot Token (DrewBot)",
        "projects": ["DrewBot"],
        "kind": "api",
        "run": lambda: discord_check.run("DREWBOT_DISCORD_TOKEN"),
        "credentials": [
            {"env": "DREWBOT_DISCORD_TOKEN", "label": "Bot token", "secret": True},
            {"env": "DISCORD_API_URL", "label": "API link", "secret": False, "url": True, "default": discord_check.DISCORD_API},
        ],
    },
    {
        "id": "discord_drewsmusicbot",
        "label": "Discord Bot Token (DrewsMusicBot)",
        "projects": ["DrewsMusicBot"],
        "kind": "api",
        "run": lambda: discord_check.run("DREWSMUSICBOT_DISCORD_TOKEN"),
        "credentials": [
            {"env": "DREWSMUSICBOT_DISCORD_TOKEN", "label": "Bot token", "secret": True},
            {"env": "DISCORD_API_URL", "label": "API link", "secret": False, "url": True, "default": discord_check.DISCORD_API},
        ],
    },
    {
        "id": "reddit_v4_discord_relay",
        "label": "Discord relay (RedditDownloaderV4)",
        "projects": ["RedditDownloaderV4"],
        "kind": "api",
        "run": discord_check.run_reddit_relay,
        "credentials": [
            {"env": "REDDITV4_WEBHOOK_URL", "label": "Webhook URL", "secret": True},
            {"env": "REDDITV4_BOT_TOKEN", "label": "Poller bot token", "secret": True},
            {"env": "REDDITV4_CHANNEL_ID", "label": "Relay channel ID", "secret": False},
            {"env": "DISCORD_API_URL", "label": "API link", "secret": False, "url": True, "default": discord_check.DISCORD_API},
        ],
    },
    {
        "id": "plex",
        "label": "Plex (via plex.tv)",
        "projects": ["PlexDiscordPresence", "PlexLibraryCompare", "YtsPlexRss"],
        "kind": "api",
        "run": plex_check.run,
        "credentials": [
            {"env": "PLEX_TOKEN", "label": "Plex token", "secret": True},
            {"env": "PLEX_SERVER_NAME", "label": "Server name", "secret": False},
            {"env": "PLEX_LIBRARIES", "label": "Libraries (comma-separated)", "secret": False},
        ],
    },
    {
        "id": "yts",
        "label": "YTS API",
        "projects": ["YtsPlexRss", "YtsSearch"],
        "kind": "api",
        "run": yts_check.run,
        "credentials": [
            {"env": "YTS_API_URL", "label": "API link", "secret": False, "url": True, "default": yts_check.YTS_LIST_MOVIES_URL},
            {"env": "YTS_MIRROR_URL", "label": "Mirror API link", "secret": False, "url": True, "default": yts_check.YTS_MIRROR_URL},
        ],
    },
    {
        "id": "reddit_rss",
        "label": "Reddit RSS feed",
        "projects": ["RedditMediaDL"],
        "kind": "scraper",
        "run": scrapers_check.run_reddit_rss,
        "credentials": [
            {"env": "REDDIT_FEED_URL", "label": "Feed link", "secret": False, "url": True, "default": scrapers_check.REDDIT_FEED_URL},
        ],
    },
    {
        "id": "atsu_moe",
        "label": "atsu.moe",
        "projects": ["MangaDL"],
        "kind": "scraper",
        "run": scrapers_check.run_atsu,
        "credentials": [
            {"env": "ATSU_URL", "label": "Site link", "secret": False, "url": True, "default": scrapers_check.ATSU_BASE},
        ],
    },
    {
        "id": "dvdsreleasedates",
        "label": "dvdsreleasedates.com",
        "projects": ["YtsSearch"],
        "kind": "scraper",
        "run": scrapers_check.run_dvdsreleasedates,
        "credentials": [
            {"env": "DVDSRELEASEDATES_URL", "label": "Site link", "secret": False, "url": True, "default": scrapers_check.DVD_HOME_URL},
        ],
    },
]


def credentials_for(check):
    return check.get("credentials", [])


def get_check(check_id):
    for check in CHECKS:
        if check["id"] == check_id:
            return check
    return None
