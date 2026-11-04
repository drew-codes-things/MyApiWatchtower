<div align="center">

# MyApiWatchtower

**A self-hosted dashboard that live-tests the APIs and services you depend on, so a breaking change gets noticed before someone else has to report it.**

[![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/flask-web-000000?style=flat-square&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

</div>

---

## What it does

A fresh install watches nothing. You add the services you care about from the page - any HTTP or HTTPS endpoint - and MyApiWatchtower checks them on demand or once a day:

- **Name, method and URL** (`GET`, `HEAD` or `POST`).
- **What counts as up**: a status code or ranges like `200-299, 301`. The default is `200-399`.
- **Optional text the response must contain**, e.g. `"status":"ok"`, so a page that answers but is broken still fails.
- **One optional secret header**, e.g. `Authorization: Bearer ...`, for APIs that need a key. It's sent with the request but never shown on the page, and it's scrubbed from any error message.
- **Timeout and tags**: tags show on the row, so you can see what a failure affects.

Each service can be edited or removed from its row. Saving or adding a service checks it straight away.

When `DISCORD_ALERT_WEBHOOK_URL` is set, a full run posts to that Discord webhook whenever a service starts failing or comes back, and stays quiet otherwise.

The results page is public and read-only. Running checks and adding, editing or removing services all need the login from `WEB_USERNAME`/`WEB_PASSWORD`; without one set, the page can't be changed from the browser at all.

## Plugins: deeper checks in Python

For checks that need more than one request - log in, then search, then download a file, checking the fields your code reads at each step - drop a Python file into a `plugins/` folder next to `app.py` (git-ignored, so your own checks never end up in this repo). Each plugin file can define:

- `CHECKS`: a list of `{"id", "label", "projects", "kind", "run"}` dicts. `kind` picks the section (`api`, `scraper` or anything else), and `run()` returns a `checks.result.CheckResult`. The `checks.probe.Probe` helper records each step with its own result and timing.
- `credentials` on a check (optional): a list of `{"env", "label", "secret"}` fields, plus `"url": True, "default": ...` for an editable API link. These get an **Edit** panel on the page; read them with `os.environ` or `checks.settings.configured_url`.
- `notes()` (optional): lines to show at the top of the page.
- `setup(app)` (optional): called once at startup, e.g. to add an OAuth callback route.

A plugin that fails to import stops the app with the plugin's name and the error, rather than silently dropping its checks.

---

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Set `WEB_USERNAME`/`WEB_PASSWORD` in `.env`, open `http://127.0.0.1:5002`, click **Sign in to edit**, and use **Add a service**. Services are saved to `services.json` next to `app.py` (owner-only permissions, git-ignored).

The status rows are public, but the edit panels (which show each service's URL and the plugin API links) only render for a signed-in viewer, so anyone who can reach the page sees names and statuses, not where things live.

### Running it for real

`python app.py` uses Flask's development server, which is fine on a laptop. For anything that stays up, run it under gunicorn (already in `requirements.txt`):

```bash
gunicorn --workers 1 --threads 8 --bind 127.0.0.1:5002 'app:create_app()'
```

Keep `--workers 1`: the run-progress state and the daily schedule live in memory, so extra workers would run the checks several times. Put it behind an HTTPS reverse proxy (Caddy, nginx) and set `SESSION_COOKIE_SECURE=true`; Basic auth over plain HTTP sends the password in the clear.

Set `SECRET_KEY` to any long random string as well (for example `python -c "import secrets; print(secrets.token_hex(32))"`). Without it a fresh key is generated on every start, which invalidates open sessions and their CSRF tokens whenever the app restarts.

`DAILY_RUN_AT` (default in `.env.example`: `05:30,17:30`) runs every check automatically at each listed time, every day, in `SCHEDULE_TIMEZONE` (default `Europe/London`). Give one time (`05:30`) for once a day, or several separated by commas. Leave it empty to only run checks by hand.

### Editing plugin credentials and API links

Plugin checks that declare `credentials` get an **Edit** panel. Saving writes the new values to `credentials.json` next to `app.py` (owner-only permissions, git-ignored), applies them straight away and re-runs that check. Values saved there take priority over `.env` and the container's environment, so a change sticks without redeploying. Secrets are never shown back: leave a secret field blank to keep its current value. API links are shown and can be edited in place; only full `http://` or `https://` links are accepted. Delete a line from `credentials.json` and restart to go back to the `.env` value or the built-in default.

### Tests

```bash
pip install pytest responses
python -m pytest tests
```

---

## How results are shown

Results persist to `results.json` (gitignored) so the dashboard has something to show between runs instead of resetting on restart. Each row shows the check's current status (ok / warn / fail / not configured / never run), the first problem it found, total latency, and its tags - so it's clear at a glance what a failure affects. Expanding a row lists every step with its own result, detail and timing; rows that warn or fail open that list automatically. The toolbar shows when the last run happened, whether it was the automatic daily run or a manual one, and when the next automatic run is due.

Failure messages are scrubbed of any secret value before they're saved or shown, since this page is public - a network error that would otherwise echo back a raw request URL (which some of these APIs put the key/token in) gets redacted first.
