import os
import secrets
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import quote
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template_string, request, session

import checks.spotify as spotify_check
from checks.result import fail
from checks.settings import configured_url
from credentials_store import InvalidCredential, apply_saved_credentials, clean_value, is_set, save_credentials
from registry import CHECKS, credentials_for, get_check
from results_store import load_results, save_results, save_single_result

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
apply_saved_credentials()

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (os.getenv("SESSION_COOKIE_SECURE") or "false").strip().lower() == "true"

WEB_USERNAME = (os.getenv("WEB_USERNAME") or "").strip()
WEB_PASSWORD = (os.getenv("WEB_PASSWORD") or "").strip()
DAILY_RUN_AT = (os.getenv("DAILY_RUN_AT") or "").strip()
SCHEDULE_TIMEZONE = ZoneInfo((os.getenv("SCHEDULE_TIMEZONE") or "Europe/London").strip())

_run_lock = threading.Lock()
_run_state = {"active": False, "completed": 0, "total": len(CHECKS)}
_schedule_state = {"next_run": None}


def auth_enabled():
    return bool(WEB_USERNAME and WEB_PASSWORD)


def requires_auth(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not auth_enabled():
            return view_func(*args, **kwargs)
        auth = request.authorization
        if auth and secrets.compare_digest(auth.username or "", WEB_USERNAME) and secrets.compare_digest(auth.password or "", WEB_PASSWORD):
            return view_func(*args, **kwargs)
        return Response(
            "Authentication required",
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="MyApiWatchtower"'},
        )
    return wrapped


@app.after_request
def set_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; script-src 'self'",
    )
    return resp


def get_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def has_valid_csrf(submitted_token):
    expected = session.get("_csrf_token")
    if not expected or not submitted_token:
        return False
    return secrets.compare_digest(expected, submitted_token)


def get_run_state():
    with _run_lock:
        return dict(_run_state)


def start_run(trigger):
    with _run_lock:
        if _run_state["active"]:
            return False
        _run_state["active"] = True
        _run_state["completed"] = 0
        _run_state["total"] = len(CHECKS)
    threading.Thread(target=run_all_checks_in_background, args=(trigger,), daemon=True).start()
    return True


def run_all_checks_in_background(trigger):
    results = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_id = {executor.submit(check["run"]): check["id"] for check in CHECKS}
        for future in as_completed(future_to_id):
            check_id = future_to_id[future]
            try:
                result = future.result()
            except Exception as error:
                result = fail(f"Check crashed: {error}")
            results[check_id] = result.to_dict()
            with _run_lock:
                _run_state["completed"] += 1

    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        save_results(checked_at, results, trigger)
    finally:
        with _run_lock:
            _run_state["active"] = False
    print(f"Checks finished ({trigger}): " + ", ".join(
        f"{check_id}={result['status']}" for check_id, result in sorted(results.items())), flush=True)


def parse_daily_time(value):
    try:
        hour, minute = (int(part) for part in value.split(":"))
    except ValueError as error:
        raise ValueError(f"DAILY_RUN_AT must look like HH:MM, got {value!r}") from error
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"DAILY_RUN_AT is out of range: {value!r}")
    return hour, minute


def next_daily_run(hour, minute, now):
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def run_daily_schedule(hour, minute):
    while True:
        next_run = next_daily_run(hour, minute, datetime.now(SCHEDULE_TIMEZONE))
        _schedule_state["next_run"] = next_run
        while datetime.now(SCHEDULE_TIMEZONE) < next_run:
            time.sleep(min(60, max(1, (next_run - datetime.now(SCHEDULE_TIMEZONE)).total_seconds())))
        if not start_run("scheduled"):
            print("Scheduled run skipped: a run was already in progress.", flush=True)


def start_daily_schedule():
    if not DAILY_RUN_AT:
        print("DAILY_RUN_AT not set; checks only run when someone presses Run checks.", flush=True)
        return
    hour, minute = parse_daily_time(DAILY_RUN_AT)
    threading.Thread(target=run_daily_schedule, args=(hour, minute), daemon=True).start()
    print(f"Checks will run automatically every day at {hour:02d}:{minute:02d} ({SCHEDULE_TIMEZONE.key}).", flush=True)


def describe_field(field):
    described = {**field, "is_set": is_set(field["env"])}
    if field.get("url"):
        described["value"] = configured_url(field["env"], field["default"])
    return described


def checks_for_template():
    stored = load_results()
    results = stored.get("results", {})
    rows = []
    for check in CHECKS:
        result = results.get(check["id"], {"status": "never_run", "message": "Not run yet", "latency_ms": None})
        rows.append({
            "id": check["id"],
            "label": check["label"],
            "projects": check["projects"],
            "kind": check["kind"],
            "status": result["status"],
            "message": result["message"],
            "latency_ms": result.get("latency_ms"),
            "steps": result.get("steps", []),
            "credentials": [describe_field(field) for field in credentials_for(check)],
        })
    return rows, stored.get("checked_at"), stored.get("trigger")


def describe_checked_at(checked_at, trigger):
    if not checked_at:
        return None
    local = datetime.fromisoformat(checked_at).astimezone(SCHEDULE_TIMEZONE)
    how = {"scheduled": "automatic daily run", "manual": "run by hand"}.get(trigger, "run")
    return f"{local:%a %d %b %Y, %H:%M} ({how})"


@app.route("/health")
def health():
    return Response('{"status": "ok"}', mimetype="application/json")


@app.route("/")
def index():
    rows, checked_at, trigger = checks_for_template()
    next_run = _schedule_state["next_run"]
    return render_template_string(
        PAGE_TEMPLATE,
        sections=[
            ("Live API checks", [row for row in rows if row["kind"] == "api"]),
            ("Website scraper checks", [row for row in rows if row["kind"] == "scraper"]),
        ],
        checked_at=describe_checked_at(checked_at, trigger),
        next_run=f"{next_run:%a %d %b, %H:%M}" if next_run else None,
        spotify_connected=spotify_check.is_user_connected(),
        csrf_token=get_csrf_token(),
        can_edit_credentials=auth_enabled(),
        notice=request.args.get("notice", ""),
    )


@app.route("/run-checks", methods=["POST"])
@requires_auth
def run_checks():
    if not has_valid_csrf(request.form.get("csrf_token")):
        return Response("Invalid CSRF token", status=403)
    start_run("manual")
    return Response("", status=204)


def run_single_check(check):
    try:
        result = check["run"]()
    except Exception as error:
        result = fail(f"Check crashed: {error}")
    save_single_result(check["id"], result.to_dict(), datetime.now(timezone.utc).isoformat())
    return result


def credential_updates(check, form):
    updates = {}
    for field in credentials_for(check):
        value = clean_value(field["label"], form.get(field["env"], ""), is_url=field.get("url", False))
        if field.get("url") and value == configured_url(field["env"], field["default"]):
            continue
        if value:
            updates[field["env"]] = value
    return updates


@app.route("/credentials/<check_id>", methods=["POST"])
@requires_auth
def edit_credentials(check_id):
    if not auth_enabled():
        return Response("Set WEB_USERNAME and WEB_PASSWORD before editing credentials from the page", status=403)
    if not has_valid_csrf(request.form.get("csrf_token")):
        return Response("Invalid CSRF token", status=403)
    check = get_check(check_id)
    if check is None or not credentials_for(check):
        return Response("No credentials to edit for that check", status=404)
    try:
        updates = credential_updates(check, request.form)
    except InvalidCredential as error:
        return redirect(f"/?notice={quote(str(error))}#{check_id}", code=303)
    if not updates:
        return redirect(f"/?notice={quote('Nothing changed - every field was left blank')}#{check_id}", code=303)
    save_credentials(updates)
    print(f"Credentials updated for {check_id}: {', '.join(sorted(updates))}", flush=True)
    result = run_single_check(check)
    notice = f"Saved {check['label']} settings and re-ran the check: {result.status.replace('_', ' ')}"
    return redirect(f"/?notice={quote(notice)}#{check_id}", code=303)


@app.route("/run-status")
def run_status():
    return jsonify(get_run_state())


@app.route("/run-progress.js")
def run_progress_js():
    return Response(RUN_PROGRESS_JS, mimetype="application/javascript")


RUN_PROGRESS_JS = """
(function() {
  const wrap = document.getElementById('run-progress-wrap');
  const fill = document.getElementById('run-progress-fill');
  const label = document.getElementById('run-progress-label');
  const btn = document.getElementById('run-btn');
  const form = document.getElementById('run-form');
  let wasActive = false;

  function render(data) {
    if (data.active) {
      wasActive = true;
      wrap.classList.add('active');
      btn.disabled = true;
      const pct = data.total ? Math.round((data.completed / data.total) * 100) : 0;
      fill.style.width = pct + '%';
      label.textContent = 'Running checks ' + data.completed + '/' + data.total;
    } else {
      wrap.classList.remove('active');
      btn.disabled = false;
      if (wasActive) {
        wasActive = false;
        window.location.reload();
      }
    }
  }

  function poll() {
    fetch('/run-status')
      .then(r => r.json())
      .then(data => { render(data); setTimeout(poll, 700); })
      .catch(() => setTimeout(poll, 2000));
  }

  if (form) {
    form.addEventListener('submit', function(e) {
      e.preventDefault();
      fetch('/run-checks', { method: 'POST', body: new FormData(form) }).then(poll);
    });
  }

  poll();
})();
"""


PAGE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MyApiWatchtower</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {
  --void: #0a0a0b;
  --panel: rgba(255, 255, 255, .045);
  --panel-raised: rgba(255, 255, 255, .07);
  --line: rgba(255, 255, 255, .08);
  --line-bright: rgba(255, 255, 255, .14);
  --signal: #e5383b;
  --phosphor: #4ade80;
  --amber: #f5a524;
  --text: #ececec;
  --text-dim: #a1a1a6;
  --text-faint: #6b6b72;
  --mono: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  --sans: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, sans-serif;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  font-family: var(--sans);
  background: radial-gradient(120% 70% at 0% 0%, rgba(160, 20, 28, .38), transparent 60%), linear-gradient(165deg, #1c0608 0%, #0d0b0c 42%, #070708 100%) fixed, var(--void);
  color: var(--text);
  line-height: 1.6;
}
.topbar { display: flex; align-items: center; gap: 1rem; padding: 1rem 1.5rem; margin-bottom: 30px; background: rgba(10, 10, 11, .55); backdrop-filter: blur(12px); border-bottom: 1px solid var(--line); }
.topbar h1 { margin: 0; font-family: var(--sans); font-size: clamp(1.15rem, 3vw, 1.35rem); font-weight: 500; }
main { max-width: 880px; margin: 0 auto; padding: 0 20px 60px; }
p.subtitle { color: var(--text-dim); }
p.spotify-note { color: var(--text-faint); font-family: var(--sans); font-size: .8rem; }
p.spotify-note code { color: var(--text-dim); }
h2.section-heading { font-family: var(--sans); font-size: 1rem; font-weight: 500; color: var(--text); border-bottom: 1px solid var(--line); padding-bottom: 10px; margin-top: 2.5rem; }
.toolbar { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; margin: 1.5rem 0; }
button {
  display: inline-flex; align-items: center; gap: .4em;
  font-family: var(--sans); background: linear-gradient(135deg, var(--signal), #7a0f14); border: 1px solid transparent; color: #fff;
  font-size: .9rem; font-weight: 500; padding: 10px 18px; border-radius: 12px;
  text-decoration: none; cursor: pointer;
  transition: filter .15s ease;
}
button:hover { filter: brightness(1.12); }
button:disabled { opacity: .5; cursor: default; filter: none; }
.checked-at { color: var(--text-faint); font-family: var(--sans); font-size: .8rem; }
.progress-wrap { display: none; align-items: center; gap: .75rem; font-family: var(--sans); font-size: .8rem; color: var(--text-dim); }
.progress-wrap.active { display: flex; }
.progress-track { width: 140px; height: 6px; background: var(--panel-raised); border-radius: 999px; overflow: hidden; }
.progress-fill { height: 100%; width: 0%; background: var(--phosphor); transition: width .3s ease; }
.row { position: relative; background: var(--panel); border: 1px solid var(--line); border-radius: 16px; backdrop-filter: blur(12px); box-shadow: 0 16px 40px rgba(0, 0, 0, .45); padding: 1rem 1.25rem; margin-bottom: 0.75rem; transition: border-color .2s ease; }
.row-top { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; }
.row-label { font-family: var(--sans); font-weight: 600; color: var(--text); }
.projects { margin-top: 0.5rem; display: flex; gap: 6px; flex-wrap: wrap; }
.badge { font-family: var(--sans); font-size: .7rem; padding: 2px 6px; border-radius: 999px; background: var(--panel-raised); color: var(--text-dim); }
.pill { font-family: var(--sans); font-size: .7rem; border-radius: 999px; padding: 3px 9px; white-space: nowrap; }
.pill-ok { color: var(--phosphor); border: 1px solid var(--phosphor); }
.pill-fail { color: var(--signal); border: 1px solid var(--signal); }
.pill-warn { color: var(--amber); border: 1px solid var(--amber); }
.pill-not_configured, .pill-never_run { color: var(--text-faint); border: 1px solid var(--text-faint); }
.steps { list-style: none; margin: .75rem 0 0; padding: .5rem 0 0; border-top: 1px dashed var(--line); font-size: .82rem; }
.steps li { display: grid; grid-template-columns: 4.5rem 1fr; gap: .15rem .75rem; padding: .2rem 0; }
.step-status { font-family: var(--sans); font-size: .7rem; padding-top: .1rem; }
.step-ok { color: var(--phosphor); }
.step-warn { color: var(--amber); }
.step-fail { color: var(--signal); }
.step-skipped { color: var(--text-faint); }
.step-name { color: var(--text); font-family: var(--sans); }
.step-detail { grid-column: 2; color: var(--text-dim); overflow-wrap: anywhere; }
.step-detail .latency { color: var(--text-faint); font-family: var(--sans); }
details.step-list summary { cursor: pointer; margin-top: .5rem; color: var(--text-faint); font-family: var(--sans); font-size: .75rem; }
.row-message { margin-top: 0.5rem; color: var(--text-dim); font-size: 0.9rem; }
.row-message .latency { color: var(--text-faint); font-family: var(--sans); }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .001ms !important; animation-iteration-count: 1 !important; transition-duration: .001ms !important; }
}
:focus-visible { outline: 2px solid var(--signal); outline-offset: 2px; }
.notice { background: var(--panel-raised); border: 1px solid var(--line-bright); border-radius: 12px; padding: .6rem 1rem; color: var(--text); font-size: .85rem; }
details.credentials summary { cursor: pointer; margin-top: .5rem; color: var(--text-faint); font-family: var(--sans); font-size: .75rem; }
.credential-form { display: grid; gap: .6rem; margin-top: .75rem; padding-top: .75rem; border-top: 1px dashed var(--line); }
.credential-form label { display: grid; gap: .25rem; font-size: .8rem; color: var(--text-dim); }
.credential-form .field-state { color: var(--text-faint); font-size: .72rem; }
.credential-form input { font-family: var(--mono); font-size: .82rem; color: var(--text); background: rgba(0, 0, 0, .35); border: 1px solid var(--line-bright); border-radius: 10px; padding: .5rem .7rem; width: 100%; }
.credentials .hint { color: var(--text-faint); font-size: .75rem; margin: 0; }
.credential-form button { justify-self: start; padding: 8px 14px; font-size: .82rem; }
</style>
</head>
<body>
  <header class="topbar">
    <h1>MyApiWatchtower</h1>
  </header>
  <main>
  <p class="subtitle">Live status of every external API drew-codes-things' projects depend on.</p>
  {% if not spotify_connected %}
  <p class="spotify-note">Spotify not connected. Check the terminal app.py was started from for the authorize link.</p>
  {% endif %}

  {% if notice %}<p class="notice" role="status">{{ notice }}</p>{% endif %}

  <div class="toolbar">
    <form id="run-form" method="post" action="/run-checks">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <button id="run-btn" type="submit">Run checks</button>
    </form>
    <div id="run-progress-wrap" class="progress-wrap">
      <div class="progress-track"><div id="run-progress-fill" class="progress-fill"></div></div>
      <span id="run-progress-label">Running checks 0/0</span>
    </div>
    {% if checked_at %}
    <span class="checked-at">Last checked {{ checked_at }}</span>
    {% else %}
    <span class="checked-at">Never run yet</span>
    {% endif %}
    {% if next_run %}<span class="checked-at">Next automatic run {{ next_run }}</span>{% endif %}
  </div>

  {% for heading, section_rows in sections %}
  <h2 class="section-heading">{{ heading }}</h2>
  {% for row in section_rows %}
  <div class="row" id="{{ row.id }}">
    <div class="row-top">
      <div>
        <div class="row-label">{{ row.label }}</div>
        <div class="projects">
          {% for project in row.projects %}
          <span class="badge">{{ project }}</span>
          {% endfor %}
        </div>
      </div>
      <span class="pill pill-{{ row.status }}">{{ row.status.replace('_', ' ') }}</span>
    </div>
    <div class="row-message">
      {{ row.message }}
      {% if row.latency_ms is not none %}<span class="latency"> - {{ row.latency_ms }}ms</span>{% endif %}
    </div>
    {% if row.steps %}
    <details class="step-list"{% if row.status in ('fail', 'warn') %} open{% endif %}>
      <summary>{{ row.steps|length }} steps</summary>
      <ul class="steps">
        {% for step in row.steps %}
        <li>
          <span class="step-status step-{{ step.status }}">{{ step.status }}</span>
          <span class="step-name">{{ step.name }}</span>
          <span class="step-detail">{{ step.detail }}{% if step.latency_ms is not none %}<span class="latency"> - {{ step.latency_ms }}ms</span>{% endif %}</span>
        </li>
        {% endfor %}
      </ul>
    </details>
    {% endif %}
    {% if row.credentials %}
    <details class="credentials">
      <summary>{{ 'Edit API link' if row.credentials|rejectattr('url')|list|length == 0 else 'Edit credentials and API link' if row.credentials|selectattr('url')|list else 'Edit credentials' }}</summary>
      {% if can_edit_credentials %}
      <form class="credential-form" method="post" action="/credentials/{{ row.id }}" autocomplete="off">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        {% for field in row.credentials %}
        <label>
          <span>{{ field.label }} <span class="field-state">({{ field.env }}{% if field.url %}{% if field.value != field.default.rstrip('/') %} - changed from the default{% endif %}{% else %} - {{ 'set' if field.is_set else 'not set' }}{% endif %})</span></span>
          {% if field.url %}
          <input name="{{ field.env }}" type="url" maxlength="512" spellcheck="false" value="{{ field.value }}" placeholder="{{ field.default }}">
          {% else %}
          <input name="{{ field.env }}" type="{{ 'password' if field.secret else 'text' }}" maxlength="512" spellcheck="false" placeholder="{{ 'Leave blank to keep the current value' if field.is_set else 'Not set yet' }}">
          {% endif %}
        </label>
        {% endfor %}
        <p class="hint">Blank fields keep their current value. Saving re-runs this check.</p>
        <button type="submit">Save and re-check</button>
      </form>
      {% else %}
      <p class="hint">Set WEB_USERNAME and WEB_PASSWORD to edit credentials from this page.</p>
      {% endif %}
    </details>
    {% endif %}
  </div>
  {% endfor %}
  {% endfor %}
  </main>
<script src="/run-progress.js"></script>
</body>
</html>
"""


_spotify_login_state = {"state": None}


def bootstrap_spotify_login():
    oauth_manager = spotify_check.make_oauth_manager()
    if oauth_manager is None:
        print("Spotify: SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET not set; Spotify checks disabled.", flush=True)
        return
    if spotify_check.is_user_connected():
        return

    state = secrets.token_urlsafe(16)
    _spotify_login_state["state"] = state
    authorize_url = oauth_manager.get_authorize_url(state=state)

    print("Spotify: open this URL in a browser to authorize (one-time setup):", flush=True)
    print(authorize_url, flush=True)
    try:
        webbrowser.open(authorize_url)
    except webbrowser.Error:
        pass


@app.route("/callback")
def spotify_callback():
    error = request.args.get("error")
    if error:
        return Response(f"Spotify authorization failed: {error}", status=400)
    returned_state = request.args.get("state")
    expected_state = _spotify_login_state.get("state")
    if not expected_state or returned_state != expected_state:
        return Response("Mismatched or expired state parameter", status=400)
    _spotify_login_state["state"] = None
    code = request.args.get("code")
    oauth_manager = spotify_check.make_oauth_manager()
    if oauth_manager is None:
        return Response("SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET are not set", status=400)
    try:
        oauth_manager.get_access_token(code, as_dict=False, check_cache=False)
    except Exception as error:
        print(f"Spotify: authorization failed - {error}", flush=True)
        return Response(f"Spotify authorization failed: {error}", status=400)
    print("Spotify: authorized.", flush=True)
    return Response("<p>Spotify authorized - you can close this tab.</p>", mimetype="text/html")


if __name__ == "__main__":
    host = os.getenv("BIND_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5002"))
    if not auth_enabled():
        print("WARNING: WEB_USERNAME/WEB_PASSWORD not set; the Run checks button is unauthenticated.", flush=True)
    bootstrap_spotify_login()
    start_daily_schedule()
    app.run(host=host, port=port, threaded=True)
