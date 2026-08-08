import os
import secrets
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template_string, request, session

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

_run_lock = threading.Lock()
_run_state = {"active": False, "completed": 0, "total": len(CHECKS)}


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


def run_all_checks_in_background():
    with _run_lock:
        _run_state["active"] = True
        _run_state["completed"] = 0
        _run_state["total"] = len(CHECKS)

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
    save_results(checked_at, results)

    with _run_lock:
        _run_state["active"] = False


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
        spotify_connected=spotify_check.is_user_connected(),
        csrf_token=get_csrf_token(),
    )


@app.route("/run-checks", methods=["POST"])
@requires_auth
def run_checks():
    if not has_valid_csrf(request.form.get("csrf_token")):
        return Response("Invalid CSRF token", status=403)
    if not get_run_state()["active"]:
        threading.Thread(target=run_all_checks_in_background, daemon=True).start()
    return Response("", status=204)


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
  --void: #07090c;
  --panel: #0e1116;
  --panel-raised: #12161c;
  --line: #1e252b;
  --line-bright: #313a42;
  --signal: #ff4d2e;
  --phosphor: #35d67c;
  --amber: #ffb020;
  --text: #d7dbde;
  --text-dim: #7d8891;
  --text-faint: #4b545c;
  --mono: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  --sans: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, sans-serif;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  font-family: var(--sans);
  background-color: var(--void);
  background-image: radial-gradient(ellipse at 50% 0%, rgba(255, 77, 46, .05), transparent 60%);
  background-attachment: fixed;
  color: var(--text);
  line-height: 1.6;
}
.topbar { display: flex; align-items: center; gap: 1rem; padding: 1rem 1.5rem; margin-bottom: 30px; background: var(--panel); border-bottom: 1px solid var(--line); }
.topbar h1 { margin: 0; font-family: var(--mono); font-size: clamp(1.2rem, 4vw, 1.6rem); font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
.topbar h1::after { content: "_"; color: var(--signal); animation: blink 1.1s steps(1) infinite; margin-left: 2px; }
main { max-width: 880px; margin: 0 auto; padding: 0 20px 60px; }
p.subtitle { color: var(--text-dim); }
p.spotify-note { color: var(--text-faint); font-family: var(--mono); font-size: .8rem; }
p.spotify-note code { color: var(--text-dim); }
h2.section-heading { font-family: var(--mono); font-size: 1rem; text-transform: uppercase; letter-spacing: .04em; color: var(--signal); border-bottom: 1px solid var(--line); padding-bottom: 10px; margin-top: 2.5rem; }
h2.section-heading::before { content: "// "; color: var(--text-faint); }
.toolbar { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; margin: 1.5rem 0; }
button {
  display: inline-flex; align-items: center; gap: .4em;
  font-family: var(--mono); background: transparent; border: 1px solid var(--signal); color: var(--signal);
  font-size: .9rem; font-weight: 500; letter-spacing: .03em; padding: 10px 18px; border-radius: 2px;
  text-decoration: none; text-transform: uppercase; cursor: pointer;
  transition: background-color .15s ease, color .15s ease;
}
button::before { content: "["; }
button::after { content: "]"; }
button:hover { background-color: var(--signal); color: var(--void); }
button:disabled { opacity: .5; cursor: default; background-color: transparent; color: var(--signal); }
.checked-at { color: var(--text-faint); font-family: var(--mono); font-size: .8rem; }
.progress-wrap { display: none; align-items: center; gap: .75rem; font-family: var(--mono); font-size: .8rem; color: var(--text-dim); }
.progress-wrap.active { display: flex; }
.progress-track { width: 140px; height: 8px; background: var(--panel-raised); border: 1px solid var(--line); border-radius: 2px; overflow: hidden; }
.progress-fill { height: 100%; width: 0%; background: var(--phosphor); transition: width .3s ease; }
.row { position: relative; background: var(--panel); border: 1px solid var(--line); border-radius: 2px; padding: 1rem 1.25rem; margin-bottom: 0.75rem; transition: border-color .2s ease; }
.row::before {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background:
    linear-gradient(var(--signal), var(--signal)) top left / 12px 2px no-repeat,
    linear-gradient(var(--signal), var(--signal)) top left / 2px 12px no-repeat,
    linear-gradient(var(--signal), var(--signal)) top right / 12px 2px no-repeat,
    linear-gradient(var(--signal), var(--signal)) top right / 2px 12px no-repeat,
    linear-gradient(var(--signal), var(--signal)) bottom left / 12px 2px no-repeat,
    linear-gradient(var(--signal), var(--signal)) bottom left / 2px 12px no-repeat,
    linear-gradient(var(--signal), var(--signal)) bottom right / 12px 2px no-repeat,
    linear-gradient(var(--signal), var(--signal)) bottom right / 2px 12px no-repeat;
  opacity: 0; transition: opacity .2s ease;
}
.row:hover::before { opacity: 1; }
.row-top { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; }
.row-label { font-family: var(--mono); font-weight: 600; color: var(--text); }
.projects { margin-top: 0.5rem; display: flex; gap: 6px; flex-wrap: wrap; }
.badge { font-family: var(--mono); font-size: .7rem; letter-spacing: .02em; padding: 2px 6px; border-radius: 1px; background: var(--panel-raised); color: var(--text-dim); border-left: 2px solid var(--text-faint); }
.badge::before { content: "["; color: var(--text-faint); }
.badge::after { content: "]"; color: var(--text-faint); }
.pill { font-family: var(--mono); font-size: .7rem; letter-spacing: .04em; text-transform: uppercase; border-radius: 2px; padding: 3px 9px; white-space: nowrap; }
.pill-ok { color: var(--phosphor); border: 1px solid var(--phosphor); }
.pill-fail { color: var(--signal); border: 1px solid var(--signal); }
.pill-not_configured, .pill-never_run { color: var(--text-faint); border: 1px solid var(--text-faint); }
.row-message { margin-top: 0.5rem; color: var(--text-dim); font-size: 0.9rem; }
.row-message .latency { color: var(--text-faint); font-family: var(--mono); }
@keyframes blink { 0%, 49% { opacity: 1; } 50%, 100% { opacity: 0; } }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .001ms !important; animation-iteration-count: 1 !important; transition-duration: .001ms !important; }
}
:focus-visible { outline: 2px solid var(--signal); outline-offset: 2px; }
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
    app.run(host=host, port=port, threaded=True)
