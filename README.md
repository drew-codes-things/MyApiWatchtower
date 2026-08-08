<div align="center">

# MyApiWatchtower

**A self-hosted dashboard that live-tests every external API drew-codes-things' other projects depend on, so a breaking change gets noticed before someone else has to report it.**

[![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/flask-web-000000?style=flat-square&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

</div>

---

## What it does

One click runs a real, live check against every third-party API that at least one other project in the catalogue actually depends on - TMDB, Jikan, Spotify (both the Client Credentials and User OAuth paths), Last.fm, two separate Discord bot tokens, Plex (via plex.tv), and YTS - plus a handful of reachability-only checks for projects that scrape a site instead of calling a documented API.

The results page is public and unauthenticated, so anyone can see current status without needing any credentials. Triggering a real run is the only thing gated behind login, since it makes live calls with real secrets.

## Checks

| Check | Depended on by | Needs |
|---|---|---|
| TMDB API | DrewBot, TmdbBackdropDL | `TMDB_API_KEY` |
| Jikan API | MediaSuggest | - |
| MediaSuggest TMDB Worker | MediaSuggest | - |
| Spotify (Client Credentials) | SpotifyInfoExtract | `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET` |
| Spotify (User OAuth) | SpotifyDashTool, DrewsMusicBot | Connect Spotify (see below) |
| Last.fm API | SpotifyDashTool | `LASTFM_API_KEY` |
| Discord Bot Token (DrewBot) | DrewBot | `DREWBOT_DISCORD_TOKEN` |
| Discord Bot Token (DrewsMusicBot) | DrewsMusicBot | `DREWSMUSICBOT_DISCORD_TOKEN` |
| Plex (via plex.tv) | PlexDiscordPresence, PlexLibraryCompare, YtsPlexRss | `PLEX_TOKEN`/`PLEX_SERVER_NAME` |
| YTS API | YtsPlexRss, YtsSearch | - |

Reachability-only (confirms the target responds, not that any scraping/parsing logic still works):

| Target | Depended on by |
|---|---|
| Reddit RSS feed | RedditMediaDL |
| atsu.moe | MangaDL |
| dvdsreleasedates.com | YtsSearch |

Any check whose env vars aren't set shows as "not configured" rather than failing, so you can run this with only a subset of the keys filled in.

---

## Setup

### 1. Install dependencies and configure

```bash
pip install -r requirements.txt
cp .env.example .env
```

Fill in whichever keys you want checked - `TMDB_API_KEY`, `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET`, `LASTFM_API_KEY`, `DREWBOT_DISCORD_TOKEN`, `DREWSMUSICBOT_DISCORD_TOKEN`, `PLEX_TOKEN`/`PLEX_SERVER_NAME`. Set `WEB_USERNAME`/`WEB_PASSWORD` to gate the Run checks button and the Spotify connect flow behind HTTP Basic Auth (recommended, since it triggers live calls with real secrets).

### 2. Spotify

The User OAuth check reuses the same Spotify Developer app already used by `SpotifyDashTool`/`DrewsMusicBot`. In that app's [dashboard](https://developer.spotify.com/dashboard), add a new Redirect URI matching where this app runs, e.g. `http://127.0.0.1:5002/spotify/callback` (or set `SPOTIFY_REDIRECT_URI` in `.env` to override it). Start the app, click **Connect Spotify** once, and the resulting token is cached to `.spotifyoauthcache` (gitignored) and reused on future runs.

The scopes requested are read-only (`playlist-read-private`, `playlist-read-collaborative`, `user-read-private`, `user-library-read`) - this only verifies the auth flow and those reads still work, it never modifies your account, even though `SpotifyDashTool` itself has write scopes.

### 3. Run

```bash
python app.py
```

Open `http://127.0.0.1:5002`, click **Run checks**.

---

## How results are shown

Results persist to `results.json` (gitignored) so the dashboard has something to show between runs instead of resetting on restart. Each row shows the check's current status (ok / fail / not configured / never run), a short message, response latency, and which of the catalogue's projects depend on it - so if Plex breaks, it's immediately clear that's three projects affected, not one.

---

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

Only the non-network logic (the check registry's shape, and results persistence) is unit tested. The checks themselves are exercised for real by clicking Run checks, the same way this user's other self-hosted tools rely on the manual verification checklist in their own READMEs rather than mocking live third-party APIs.
