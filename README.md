<div align="center">

# MyApiWatchtower

**A self-hosted dashboard that live-tests every external API drew-codes-things' other projects depend on, so a breaking change gets noticed before someone else has to report it.**

[![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/flask-web-000000?style=flat-square&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

</div>

---

## What it does

One click (or the automatic daily run) checks every third-party API that at least one other project in the catalogue depends on - TMDB, AniList, Spotify (both the Client Credentials and User OAuth paths), Last.fm, the Discord bots, RedditDownloaderV4's Discord relay, Plex (via plex.tv), and YTS - plus the websites that projects scrape instead of calling a documented API.

Each check is a series of steps that copy what the dependent projects actually do: the same endpoints, parameters and headers, then the exact response fields their code reads. So a result doesn't just say "TMDB is up", it says which project's request broke and why - for example "DrewBot TV search: field 'first_air_date' is missing", "HTTP 521 (Cloudflare 521 - their origin server is down)", or "YtsSearch release year: the title/year markup changed". Each step passes, warns (it works, but a project has to fall back, like a project falling back to a slower path), fails, or is skipped because an earlier step it needed failed.

The results page is public and unauthenticated, so anyone can see current status without needing any credentials. Triggering a real run is the only thing gated behind login, since it makes live calls with real secrets.

## Checks

| Check | Depended on by | What the steps cover | Needs |
|---|---|---|---|
| TMDB API | DrewBot, TmdbBackdropDL | configuration, `/search/movie` and `/search/tv` with the fields DrewBot's embed shows, `/search/multi`, `/movie/{id}/images`, and downloading an original-size backdrop | `TMDB_API_KEY` |
| AniList API | YtsPlexRss | the anime filter's GraphQL search: a known anime film still matches as an anime movie of the right year, and a live-action film doesn't (Jikan went down in October 2026) | - |
| MediaSuggest TMDB Worker | MediaSuggest | movie, movie-with-genre, TV and anime picks, including that anime picks really are Japanese animation | - |
| Spotify (Client Credentials) | SpotifyInfoExtract | app token, search, artist, albums, album tracks | `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET` |
| Spotify (User OAuth) | SpotifyDashTool, DrewsMusicBot | refreshing the saved login, `/me`, playlist entry counts, DrewsMusicBot's `/playlists/{id}/items` read, `/tracks/{id}` | one-time login on first `python app.py` (see below) |
| Last.fm API | SpotifyDashTool | `track.getInfo` tags and the `artist.getTopTags` fallback | `LASTFM_API_KEY` |
| Discord Bot Token (DrewBot / DrewsMusicBot) | DrewBot, DrewsMusicBot | the token, and gateway logins left (a reconnect loop shows up here) | `DREWBOT_DISCORD_TOKEN`, `DREWSMUSICBOT_DISCORD_TOKEN` |
| Discord relay (RedditDownloaderV4) | RedditDownloaderV4 | the Devvit app's webhook posts into the channel discord-poller reads, the poller bot's token and Message Content Intent, and reading `RELAY:` messages | `REDDITV4_WEBHOOK_URL`, `REDDITV4_BOT_TOKEN`, `REDDITV4_CHANNEL_ID` |
| Plex (via plex.tv) | PlexDiscordPresence, PlexLibraryCompare, YtsPlexRss | sign-in, connecting to the server, each library other projects open by name, and the item lookup PlexDiscordPresence makes | `PLEX_TOKEN`/`PLEX_SERVER_NAME` (optionally `PLEX_LIBRARIES`) |
| YTS API | YtsPlexRss, YtsSearch | YtsPlexRss's 1080p scan, YtsSearch's title search with every torrent field, and downloading a real `.torrent` from yts.gg | - |

Website scraper checks (same requests and parsing as the projects, so a markup change is caught, not just downtime):

| Target | Depended on by | What the steps cover |
|---|---|---|
| Reddit RSS feed | RedditMediaDL | the feed with RedditMediaDL's own User-Agent, the Atom fields it reads, and that posts still carry Reddit image links |
| atsu.moe | MangaDL | a manga from the sitemap through `/api/manga/info`, `/api/manga/page`, `/api/read/chapter`, and a page image from the CDN |
| dvdsreleasedates.com | YtsSearch | an exact-title redirect and release status, the year markup on an upcoming film, and parsing a results list |

Any check whose env vars aren't set shows as "not configured" rather than failing, so you can run this with only a subset of the keys filled in.

---

## Setup

### 1. Install dependencies and configure

```bash
pip install -r requirements.txt
cp .env.example .env
```

Fill in whichever keys you want checked - `TMDB_API_KEY`, `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET`, `LASTFM_API_KEY`, `DREWBOT_DISCORD_TOKEN`, `DREWSMUSICBOT_DISCORD_TOKEN`, `REDDITV4_WEBHOOK_URL`/`REDDITV4_BOT_TOKEN`/`REDDITV4_CHANNEL_ID`, `PLEX_TOKEN`/`PLEX_SERVER_NAME`.

`DAILY_RUN_AT` (default in `.env.example`: `05:30`) runs every check automatically once a day at that time in `SCHEDULE_TIMEZONE` (default `Europe/London`), lined up with the server's nightly backup. Leave it empty to only run checks by hand. Set `WEB_USERNAME`/`WEB_PASSWORD` to gate the Run checks button behind HTTP Basic Auth (recommended, since it triggers live calls with real secrets).

### Editing credentials and API links from the page

Each check has an **Edit** panel for the keys, tokens and API links it uses. Saving writes the new values to `credentials.json` next to `app.py` (owner-only permissions, git-ignored), applies them straight away and re-runs that check. Values saved there take priority over `.env` and the container's environment, so a change sticks without redeploying. Secrets are never shown back: leave a secret field blank to keep its current value. API links are shown and can be edited in place; only full `http://` or `https://` links are accepted. Delete a line from `credentials.json` and restart to go back to the `.env` value or the built-in default.

Editing only works when `WEB_USERNAME`/`WEB_PASSWORD` are set, so nobody without the login can swap your keys. This only changes what MyApiWatchtower tests with; the projects themselves keep their own settings.

### 2. Spotify

The User OAuth check reuses the same Spotify Developer app already used by `SpotifyDashTool`/`DrewsMusicBot`. In that app's [dashboard](https://developer.spotify.com/dashboard), add a new Redirect URI matching wherever this app runs: `http://127.0.0.1:5002/callback` (or whatever port you set `PORT` to in `.env`, if you'd rather not use the default 5002). The callback has to land on this same port since that's the only one the app is actually listening on.

The scopes requested are read-only (`playlist-read-private`, `playlist-read-collaborative`, `user-read-private`, `user-library-read`) - this only verifies the auth flow and those reads still work, it never modifies your account, even though `SpotifyDashTool` itself has write scopes.

### 3. Run

```bash
python app.py
```

If Spotify keys are set and you haven't connected an account yet, an authorize link is printed to this terminal on startup and opened in your default browser automatically (if one's available on this machine). Log in, and the browser redirects back into the running app's own `/callback` route to finish the exchange - no separate step needed. The resulting token is cached to `.spotifyoauthcache` (gitignored) and reused on future runs, so this only happens again if that token is later revoked from your Spotify account settings.

Open `http://127.0.0.1:5002`, click **Run checks**. A progress bar tracks how many of the checks have finished; the page reloads with results once it's done.

---

## How results are shown

Results persist to `results.json` (gitignored) so the dashboard has something to show between runs instead of resetting on restart. Each row shows the check's current status (ok / warn / fail / not configured / never run), the first problem it found, total latency, and which of the catalogue's projects depend on it - so if Plex breaks, it's immediately clear that's three projects affected, not one. Expanding a row lists every step with its own result, detail and timing; rows that warn or fail open that list automatically. The toolbar shows when the last run happened, whether it was the automatic daily run or a manual one, and when the next automatic run is due.

Failure messages are scrubbed of any secret value before they're saved or shown, since this page is public - a network error that would otherwise echo back a raw request URL (which some of these APIs put the key/token in) gets redacted first.
