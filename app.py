import os
import secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, Response, redirect, render_template_string, request, session, url_for

import checks.spotify as spotify_check
from checks.result import fail
from registry import CHECKS
from results_store import load_results, save_results

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (os.getenv("SESSION_COOKIE_SECURE") or "false").strip().lower() == "true"

WEB_USERNAME = (os.getenv("WEB_USERNAME") or "").strip()
WEB_PASSWORD = (os.getenv("WEB_PASSWORD") or "").strip()


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
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'",
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


def run_all_checks():
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
    return results


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
        })
    return rows, stored.get("checked_at")


@app.route("/health")
def health():
    return Response('{"status": "ok"}', mimetype="application/json")


@app.route("/")
def index():
    rows, checked_at = checks_for_template()
    return render_template_string(
        PAGE_TEMPLATE,
        api_rows=[row for row in rows if row["kind"] == "api"],
        reachability_rows=[row for row in rows if row["kind"] == "reachability"],
        checked_at=checked_at,
        auth_enabled=auth_enabled(),
        spotify_connected=spotify_check.is_user_connected(),
        csrf_token=get_csrf_token(),
    )


@app.route("/run-checks", methods=["POST"])
@requires_auth
def run_checks():
    if not has_valid_csrf(request.form.get("csrf_token")):
        return Response("Invalid CSRF token", status=403)
    results = run_all_checks()
    checked_at = datetime.now(timezone.utc).isoformat()
    save_results(checked_at, results)
    return redirect(url_for("index"))


@app.route("/spotify/connect")
@requires_auth
def spotify_connect():
    oauth_manager = spotify_check.make_oauth_manager()
    if oauth_manager is None:
        return Response("SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET are not set", status=400)
    state = secrets.token_urlsafe(16)
    session["_spotify_oauth_state"] = state
    return redirect(oauth_manager.get_authorize_url(state=state))


@app.route("/spotify/callback")
def spotify_callback():
    error = request.args.get("error")
    if error:
        return Response(f"Spotify authorization failed: {error}", status=400)
    returned_state = request.args.get("state")
    expected_state = session.pop("_spotify_oauth_state", None)
    if not expected_state or returned_state != expected_state:
        return Response("Mismatched state parameter", status=400)
    code = request.args.get("code")
    oauth_manager = spotify_check.make_oauth_manager()
    if oauth_manager is None:
        return Response("SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET are not set", status=400)
    oauth_manager.get_access_token(code, as_dict=False, check_cache=False)
    return redirect(url_for("index"))


PAGE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MyApiWatchtower</title>
<style>
:root {
  --bg: #0f1115;
  --card: #171a21;
  --border: #2a2e38;
  --text: #e6e8ec;
  --muted: #8a8f9c;
  --ok: #2ecc71;
  --fail: #e0525a;
  --neutral: #565c68;
}
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem 1rem 4rem; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width: 880px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
p.subtitle { color: var(--muted); margin-top: 0; }
h2.section-heading { font-size: 1rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); margin-top: 2.5rem; }
.toolbar { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; margin: 1.5rem 0; }
button, .button-link { background: #3d6bff; color: #fff; border: none; padding: 0.6rem 1.1rem; border-radius: 6px; font-size: 0.95rem; cursor: pointer; text-decoration: none; display: inline-block; }
button:hover, .button-link:hover { background: #2f57d6; }
.checked-at { color: var(--muted); font-size: 0.85rem; }
.row { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 0.75rem; }
.row-top { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; }
.row-label { font-weight: 600; }
.projects { margin-top: 0.4rem; display: flex; gap: 0.4rem; flex-wrap: wrap; }
.badge { background: #232733; color: var(--muted); border-radius: 999px; padding: 0.15rem 0.6rem; font-size: 0.75rem; }
.pill { border-radius: 999px; padding: 0.2rem 0.7rem; font-size: 0.8rem; font-weight: 600; }
.pill-ok { background: rgba(46, 204, 113, 0.15); color: var(--ok); }
.pill-fail { background: rgba(224, 82, 90, 0.15); color: var(--fail); }
.pill-not_configured, .pill-never_run { background: rgba(86, 92, 104, 0.25); color: var(--neutral); }
.row-message { margin-top: 0.5rem; color: var(--muted); font-size: 0.9rem; }
.row-message .latency { color: #555b68; }
</style>
</head>
<body>
<main>
  <h1>MyApiWatchtower</h1>
  <p class="subtitle">Live status of every external API drew-codes-things' projects depend on.</p>

  <div class="toolbar">
    <form method="post" action="{{ url_for('run_checks') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <button type="submit">Run checks</button>
    </form>
    {% if not spotify_connected %}
    <a class="button-link" href="{{ url_for('spotify_connect') }}">Connect Spotify</a>
    {% endif %}
    {% if checked_at %}
    <span class="checked-at">Last checked {{ checked_at }}</span>
    {% else %}
    <span class="checked-at">Never run yet</span>
    {% endif %}
  </div>

  <h2 class="section-heading">Live API checks</h2>
  {% for row in api_rows %}
  <div class="row">
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
  </div>
  {% endfor %}

  <h2 class="section-heading">Reachability-only checks</h2>
  {% for row in reachability_rows %}
  <div class="row">
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
  </div>
  {% endfor %}
</main>
</body>
</html>
"""


if __name__ == "__main__":
    host = os.getenv("BIND_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5002"))
    if not auth_enabled():
        print("WARNING: WEB_USERNAME/WEB_PASSWORD not set; the Run checks button is unauthenticated.")
    app.run(host=host, port=port)
