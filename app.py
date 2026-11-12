import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template_string, request, session

from alerts import send_alert, status_changes
from checks.result import fail
from checks.settings import configured_url
from credentials_store import InvalidCredential, apply_saved_credentials, clean_value, is_set, save_credentials
from registry import all_checks, credentials_for, get_check, plugin_notes, section_title, setup_plugins
from results_store import load_results, remove_result, save_results, save_single_result
from services_store import InvalidService, delete_service, get_service, save_service, service_from_form

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
apply_saved_credentials()

app = Flask(__name__)
app.secret_key = (os.getenv("SECRET_KEY") or "").strip() or secrets.token_hex(32)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (os.getenv("SESSION_COOKIE_SECURE") or "false").strip().lower() == "true"

WEB_USERNAME = (os.getenv("WEB_USERNAME") or "").strip()
WEB_PASSWORD = (os.getenv("WEB_PASSWORD") or "").strip()
DAILY_RUN_AT = (os.getenv("DAILY_RUN_AT") or "").strip()
SCHEDULE_TIMEZONE = ZoneInfo((os.getenv("SCHEDULE_TIMEZONE") or "Europe/London").strip())

_run_lock = threading.Lock()
_run_state = {"active": False, "completed": 0, "total": 0}
_schedule_state = {"next_run": None}


def auth_enabled():
    return bool(WEB_USERNAME and WEB_PASSWORD)


def viewer_is_authenticated():
    if not auth_enabled():
        return False
    auth = request.authorization
    return bool(
        auth
        and secrets.compare_digest(auth.username or "", WEB_USERNAME)
        and secrets.compare_digest(auth.password or "", WEB_PASSWORD)
    )


def requires_auth(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not auth_enabled() or viewer_is_authenticated():
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
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; script-src 'self'",
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
        checks = all_checks()
        _run_state["total"] = len(checks)
    threading.Thread(target=run_all_checks_in_background, args=(trigger, checks), daemon=True).start()
    return True


def run_all_checks_in_background(trigger, checks):
    results = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_id = {executor.submit(check["run"]): check["id"] for check in checks}
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
    previous = load_results().get("results", {})
    try:
        save_results(checked_at, results, trigger)
        send_alert(status_changes(previous, results, {check["id"]: check["label"] for check in checks}))
    finally:
        with _run_lock:
            _run_state["active"] = False
    print(f"Checks finished ({trigger}): " + ", ".join(
        f"{check_id}={result['status']}" for check_id, result in sorted(results.items())), flush=True)


def parse_daily_time(value):
    try:
        hour, minute = (int(part) for part in value.split(":"))
    except ValueError as error:
        raise ValueError(f"DAILY_RUN_AT times must look like HH:MM, got {value!r}") from error
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"DAILY_RUN_AT time is out of range: {value!r}")
    return hour, minute


def parse_daily_times(value):
    times = sorted({parse_daily_time(part.strip()) for part in value.split(",") if part.strip()})
    if not times:
        raise ValueError(f"DAILY_RUN_AT has no times in it: {value!r}")
    return times


def next_daily_run(times, now):
    candidates = []
    for hour, minute in times:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        candidates.append(candidate)
    return min(candidates)


def run_daily_schedule(times):
    while True:
        next_run = next_daily_run(times, datetime.now(SCHEDULE_TIMEZONE))
        _schedule_state["next_run"] = next_run
        while datetime.now(SCHEDULE_TIMEZONE) < next_run:
            time.sleep(min(60, max(1, (next_run - datetime.now(SCHEDULE_TIMEZONE)).total_seconds())))
        if not start_run("scheduled"):
            print("Scheduled run skipped: a run was already in progress.", flush=True)


def start_daily_schedule():
    if not DAILY_RUN_AT:
        print("DAILY_RUN_AT not set; checks only run when someone presses Run checks.", flush=True)
        return
    times = parse_daily_times(DAILY_RUN_AT)
    threading.Thread(target=run_daily_schedule, args=(times,), daemon=True).start()
    listed = ", ".join(f"{hour:02d}:{minute:02d}" for hour, minute in times)
    print(f"Checks will run automatically every day at {listed} ({SCHEDULE_TIMEZONE.key}).", flush=True)


def describe_field(field):
    described = {**field, "is_set": is_set(field["env"])}
    if field.get("url"):
        described["value"] = configured_url(field["env"], field["default"])
    return described


def public_service_fields(service):
    if service is None:
        return None
    return {**{key: value for key, value in service.items() if key != "header_value"},
            "has_header_value": bool(service.get("header_value"))}


def sections_for(rows):
    kinds = []
    for row in rows:
        if row["kind"] not in kinds:
            kinds.append(row["kind"])
    return [(section_title(kind), [row for row in rows if row["kind"] == kind]) for kind in kinds]


def checks_for_template():
    stored = load_results()
    results = stored.get("results", {})
    rows = []
    for check in all_checks():
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
            "service": public_service_fields(check.get("service")),
        })
    return rows, stored.get("checked_at"), stored.get("trigger")


def hide_url(text, url):
    if not text or not url:
        return text
    host = urlsplit(url).hostname or "the service"
    return text.replace(url, host).replace(url.rstrip("/"), host)


def redact_row_urls(row):
    urls = [field["value"] for field in row["credentials"] if field.get("url") and field.get("value")]
    if row["service"]:
        urls.append(row["service"]["url"])
    for url in urls:
        row["message"] = hide_url(row["message"], url)
        row["steps"] = [{**step, "name": hide_url(step.get("name"), url), "detail": hide_url(step.get("detail"), url)} for step in row["steps"]]
    return row


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
    signed_in = viewer_is_authenticated()
    if not signed_in:
        rows = [redact_row_urls(row) for row in rows]
    return render_template_string(
        PAGE_TEMPLATE,
        sections=sections_for(rows),
        checked_at=describe_checked_at(checked_at, trigger),
        next_run=f"{next_run:%a %d %b, %H:%M}" if next_run else None,
        notes=plugin_notes(),
        csrf_token=get_csrf_token(),
        can_edit_credentials=signed_in,
        auth_configured=auth_enabled(),
        can_sign_in=auth_enabled() and not signed_in,
        notice=request.args.get("notice", ""),
    )


@app.route("/login")
@requires_auth
def login():
    return redirect("/")


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


def editing_refused():
    if not auth_enabled():
        return Response("Set WEB_USERNAME and WEB_PASSWORD before editing from the page", status=403)
    if not has_valid_csrf(request.form.get("csrf_token")):
        return Response("Invalid CSRF token", status=403)
    return None


def back_to_page(notice, anchor=""):
    return redirect(f"/?notice={quote(notice)}" + (f"#{anchor}" if anchor else ""), code=303)


@app.route("/credentials/<check_id>", methods=["POST"])
@requires_auth
def edit_credentials(check_id):
    refused = editing_refused()
    if refused:
        return refused
    check = get_check(check_id)
    if check is None or not credentials_for(check):
        return Response("No credentials to edit for that check", status=404)
    try:
        updates = credential_updates(check, request.form)
    except InvalidCredential as error:
        return back_to_page(str(error), check_id)
    if not updates:
        return back_to_page("Nothing changed - every field was left blank", check_id)
    save_credentials(updates)
    print(f"Credentials updated for {check_id}: {', '.join(sorted(updates))}", flush=True)
    result = run_single_check(check)
    return back_to_page(f"Saved {check['label']} settings and re-ran the check: {result.status.replace('_', ' ')}", check_id)


@app.route("/services", methods=["POST"])
@requires_auth
def add_service():
    refused = editing_refused()
    if refused:
        return refused
    try:
        service = save_service(service_from_form(request.form))
    except InvalidService as error:
        return back_to_page(str(error), "add-service")
    print(f"Service added: {service['id']}", flush=True)
    result = run_single_check(get_check(service["id"]))
    return back_to_page(f"Added {service['name']} and checked it: {result.status.replace('_', ' ')}", service["id"])


@app.route("/services/<service_id>", methods=["POST"])
@requires_auth
def edit_service(service_id):
    refused = editing_refused()
    if refused:
        return refused
    existing = get_service(service_id)
    if existing is None:
        return Response("No service with that id", status=404)
    try:
        service = save_service(service_from_form(request.form, existing))
    except InvalidService as error:
        return back_to_page(str(error), service_id)
    print(f"Service edited: {service_id}", flush=True)
    result = run_single_check(get_check(service_id))
    return back_to_page(f"Saved {service['name']} and re-checked it: {result.status.replace('_', ' ')}", service_id)


@app.route("/services/<service_id>/delete", methods=["POST"])
@requires_auth
def remove_service(service_id):
    refused = editing_refused()
    if refused:
        return refused
    existing = get_service(service_id)
    if existing is None or not delete_service(service_id):
        return Response("No service with that id", status=404)
    remove_result(service_id)
    print(f"Service removed: {service_id}", flush=True)
    return back_to_page(f"Removed {existing['name']}")


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
<style>
@font-face { font-family: "IBM Plex Sans"; font-style: normal; font-weight: 400; font-display: swap; src: url(/static/fonts/ibm-plex-sans-latin-400-normal.woff2) format("woff2"); }
@font-face { font-family: "IBM Plex Sans"; font-style: normal; font-weight: 500; font-display: swap; src: url(/static/fonts/ibm-plex-sans-latin-500-normal.woff2) format("woff2"); }
@font-face { font-family: "IBM Plex Sans"; font-style: normal; font-weight: 600; font-display: swap; src: url(/static/fonts/ibm-plex-sans-latin-600-normal.woff2) format("woff2"); }
:root {
  color-scheme: dark;
  --bg: #19191c;
  --surface: #222226;
  --text: #eeeef0;
  --muted: #b4b4bd;
  --faint: #9a9aa5;
  --accent: #e5383b;
  --accent-fill: #d52c30;
  --accent-fill-hover: #e5383b;
  --accent-text: #ff7a7c;
  --on-accent: #ffffff;
  --ok: #5fd990;
  --warn: #f2b552;
  --line: color-mix(in srgb, var(--text) 10%, transparent);
  --line-strong: color-mix(in srgb, var(--text) 18%, transparent);
  --line-control: color-mix(in srgb, var(--text) 46%, transparent);
  --hover: color-mix(in srgb, var(--text) 7%, transparent);
  --accent-tint: color-mix(in srgb, var(--accent) 14%, transparent);
  --ok-tint: color-mix(in srgb, var(--ok) 12%, transparent);
  --warn-tint: color-mix(in srgb, var(--warn) 12%, transparent);
  --shadow-2: 0 24px 64px rgba(0, 0, 0, .5), 0 2px 6px rgba(0, 0, 0, .3);
  --r-md: 12px;
  --r-lg: 16px;
  --r-xl: 24px;
  --r-pill: 999px;
  --control-h: 36px;
  --touch: 44px;
  --sans: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --fs-xs: .75rem;
  --fs-sm: .8125rem;
  --fs-md: .875rem;
  --fs-lg: .9375rem;
  --dur: .16s;
  --dur-rise: .24s;
  --ease: cubic-bezier(.2, .7, .3, 1);
  --focus: color-mix(in srgb, var(--accent-text) 72%, transparent);
  --ring: 0 0 0 3px var(--accent-tint);
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; text-size-adjust: 100%; background: var(--bg); }
body {
  margin: 0;
  min-height: 100vh;
  font-family: var(--sans);
  font-size: var(--fs-md);
  line-height: 1.55;
  background: var(--bg);
  color: var(--text);
  -webkit-font-smoothing: antialiased;
  -webkit-tap-highlight-color: transparent;
}
a { color: var(--accent-text); text-underline-offset: 3px; }
::selection { background: color-mix(in srgb, var(--accent) 35%, transparent); }
* { scrollbar-width: thin; scrollbar-color: var(--line-strong) transparent; }
.topbar { display: flex; align-items: center; gap: 16px; padding: 14px max(24px, env(safe-area-inset-left)); padding-top: calc(14px + env(safe-area-inset-top)); margin-bottom: 32px; background: var(--bg); border-bottom: 1px solid var(--line); }
.topbar h1 { margin: 0; font-size: 1.0625rem; font-weight: 600; letter-spacing: -.012em; }
.wordmark-accent { color: var(--accent-text); }
main { max-width: 880px; margin: 0 auto; padding: 0 24px calc(64px + env(safe-area-inset-bottom)); }
p.subtitle { margin: 0 0 4px; color: var(--muted); font-size: var(--fs-lg); }
p.plugin-note { margin: 4px 0; color: var(--faint); font-size: var(--fs-sm); }
h2.section-heading { margin: 32px 0 12px; padding: 0; border: none; font-size: var(--fs-lg); font-weight: 600; letter-spacing: -.012em; color: var(--text); }
.toolbar { display: flex; align-items: center; gap: 12px 16px; flex-wrap: wrap; margin: 24px 0; }
.sign-in { display: inline-flex; align-items: center; height: var(--control-h); margin-left: auto; padding: 0 16px; border: 1px solid var(--line-strong); border-radius: var(--r-pill); color: var(--text); font-size: var(--fs-sm); font-weight: 500; text-decoration: none; transition: background-color var(--dur) var(--ease); }
.sign-in:hover { background: var(--hover); color: var(--text); }
button {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  height: var(--control-h); padding: 0 16px;
  font-family: var(--sans); font-size: var(--fs-sm); font-weight: 600; white-space: nowrap;
  background: var(--accent-fill); border: 1px solid transparent; color: var(--on-accent); border-radius: var(--r-pill);
  text-decoration: none; cursor: pointer;
  transition: background-color var(--dur) var(--ease), border-color var(--dur) var(--ease), transform var(--dur) var(--ease), opacity var(--dur) var(--ease);
}
button:hover:not(:disabled) { background: var(--accent-fill-hover); }
button:active:not(:disabled) { transform: scale(.97); }
button:disabled { opacity: .45; cursor: not-allowed; }
.checked-at { color: var(--faint); font-size: var(--fs-sm); }
.progress-wrap { display: none; align-items: center; gap: 12px; font-size: var(--fs-sm); color: var(--muted); }
.progress-wrap.active { display: flex; }
.progress-track { width: 140px; height: 4px; background: var(--line-strong); border-radius: var(--r-pill); overflow: hidden; }
.progress-fill { height: 100%; width: 0%; background: var(--accent-fill); transition: width .3s var(--ease); }
.row { position: relative; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-lg); padding: 16px 20px; margin-bottom: 12px; animation: rise-in var(--dur-rise) var(--ease); }
.row-top { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.row-label { font-weight: 600; letter-spacing: -.012em; color: var(--text); }
.projects { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
.projects:empty { display: none; }
.badge { font-size: var(--fs-xs); padding: 2px 8px; border: 1px solid var(--line-strong); border-radius: var(--r-pill); color: var(--muted); }
.pill { font-size: var(--fs-xs); font-weight: 500; border-radius: var(--r-pill); padding: 3px 10px; white-space: nowrap; }
.pill-ok { color: var(--ok); background: var(--ok-tint); }
.pill-fail { color: var(--accent-text); background: var(--accent-tint); }
.pill-warn { color: var(--warn); background: var(--warn-tint); }
.pill-not_configured, .pill-never_run { color: var(--muted); border: 1px solid var(--line-strong); }
.steps { list-style: none; margin: 12px 0 0; padding: 12px 0 0; border-top: 1px solid var(--line); font-size: var(--fs-sm); }
.steps li { display: grid; grid-template-columns: 4.5rem 1fr; gap: 2px 12px; padding: 4px 0; }
.step-status { font-size: var(--fs-xs); font-weight: 500; padding-top: 1px; }
.step-ok { color: var(--ok); }
.step-warn { color: var(--warn); }
.step-fail { color: var(--accent-text); }
.step-skipped { color: var(--faint); }
.step-name { color: var(--text); }
.step-detail { grid-column: 2; color: var(--muted); overflow-wrap: anywhere; }
.latency { color: var(--faint); }
details > summary { cursor: pointer; width: fit-content; border-radius: var(--r-md); transition: color var(--dur) var(--ease); }
details.step-list summary, details.credentials summary { margin-top: 8px; color: var(--faint); font-size: var(--fs-sm); }
details.step-list summary:hover, details.credentials summary:hover { color: var(--text); }
.row-message { margin-top: 8px; color: var(--muted); overflow-wrap: anywhere; }
@keyframes rise-in { from { opacity: 0; transform: translateY(8px); } }
@keyframes pop-in { from { opacity: 0; transform: translate(-50%, -4px) scale(.98); } }
@keyframes pop-out { to { opacity: 0; visibility: hidden; } }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .001ms !important; animation-iteration-count: 1 !important; transition-duration: .001ms !important; }
}
:focus { outline: none; }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.notice { position: fixed; top: calc(16px + env(safe-area-inset-top)); left: 50%; z-index: 400; max-width: calc(100vw - 32px); width: max-content; margin: 0; padding: 12px 16px; border-radius: var(--r-xl); background: var(--text); color: var(--bg); box-shadow: var(--shadow-2); font-size: var(--fs-sm); font-weight: 500; line-height: 1.35; transform: translateX(-50%); animation: pop-in var(--dur-rise) var(--ease), pop-out var(--dur) var(--ease) 6s forwards; }
.credential-form { display: grid; gap: 12px; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line); }
.credential-form label { display: grid; gap: 6px; font-size: var(--fs-sm); font-weight: 500; color: var(--text); }
.credential-form .field-state { color: var(--faint); font-size: var(--fs-xs); font-weight: 400; }
.credential-form input, .credential-form select { width: 100%; min-width: 0; height: var(--control-h); padding: 0 16px; font-family: var(--sans); font-size: var(--fs-sm); color: var(--text); background-color: var(--bg); border: 1px solid var(--line-strong); border-radius: var(--r-pill); appearance: none; -webkit-appearance: none; transition: border-color var(--dur) var(--ease), box-shadow var(--dur) var(--ease); }
.credential-form select { padding-right: 36px; background-image: linear-gradient(45deg, transparent 50%, var(--muted) 50%), linear-gradient(135deg, var(--muted) 50%, transparent 50%); background-repeat: no-repeat; background-position: calc(100% - 20px) 52%, calc(100% - 15px) 52%; background-size: 5px 5px; cursor: pointer; }
.credential-form select option { background: var(--surface); color: var(--text); }
.credential-form input:hover, .credential-form select:hover { border-color: var(--line-control); }
.credential-form input:focus-visible, .credential-form select:focus-visible { outline: none; border-color: var(--focus); box-shadow: var(--ring); }
.credential-form input::placeholder { color: var(--faint); opacity: 1; }
.credentials .hint { color: var(--faint); font-size: var(--fs-sm); margin: 8px 0 0; }
.credential-form button { justify-self: start; }
.field-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 560px) { .field-pair { grid-template-columns: 1fr; } }
.form-actions { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.form-actions .hint { margin: 0; }
button.button-quiet { background: transparent; border-color: color-mix(in srgb, var(--accent-text) 45%, transparent); color: var(--accent-text); font-weight: 500; }
button.button-quiet:hover:not(:disabled) { background: var(--accent-tint); }
details.add-service { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-lg); padding: 12px 20px; margin-bottom: 24px; }
details.add-service > summary { font-weight: 600; letter-spacing: -.012em; padding-block: 2px; }
details.add-service[open] > summary { margin-bottom: 4px; }
.empty-state { color: var(--muted); border: 1px dashed var(--line-strong); border-radius: var(--r-lg); padding: 16px; text-align: center; }
@media (max-width: 640px) {
  .topbar { padding-left: 16px; padding-right: 16px; margin-bottom: 24px; }
  main { padding-left: 16px; padding-right: 16px; }
  .row, details.add-service { padding-left: 16px; padding-right: 16px; }
  .sign-in { margin-left: 0; }
}
@media (max-width: 820px), (pointer: coarse) {
  button, .sign-in, .credential-form input, .credential-form select { height: var(--touch); }
  .credential-form input, .credential-form select { font-size: 16px; }
  details > summary { padding-block: 10px; }
}
</style>
</head>
<body>
  <header class="topbar">
    <h1>MyApi<span class="wordmark-accent">Watchtower</span></h1>
  </header>
  <main>
  <p class="subtitle">Live status of the APIs and services you depend on.</p>
  {% for note in notes %}
  <p class="plugin-note">{{ note }}</p>
  {% endfor %}

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
    {% if can_sign_in %}<a class="sign-in" href="/login">Sign in to edit</a>{% endif %}
  </div>

  {% if can_edit_credentials %}
  <details class="add-service" id="add-service">
    <summary>Add a service</summary>
    {% set svc = none %}
    <form class="credential-form" method="post" action="/services" autocomplete="off">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <div class="field-pair">
          <label><span>Name</span><input name="name" required maxlength="60" value="{{ svc.name if svc else '' }}" placeholder="My API"></label>
          <label><span>Method</span><select name="method">{% for method in ['GET', 'HEAD', 'POST'] %}<option{% if (svc.method if svc else 'GET') == method %} selected{% endif %}>{{ method }}</option>{% endfor %}</select></label>
        </div>
        <label><span>URL</span><input name="url" type="url" required maxlength="512" spellcheck="false" value="{{ svc.url if svc else '' }}" placeholder="https://api.example.com/health"></label>
        <div class="field-pair">
          <label><span>Counts as up when the status is</span><input name="expected_status" maxlength="60" value="{{ svc.expected_status if svc else '200-399' }}" placeholder="200-399"></label>
          <label><span>Timeout (seconds)</span><input name="timeout" inputmode="numeric" maxlength="2" value="{{ svc.timeout if svc else 15 }}"></label>
        </div>
        <label><span>Response must contain <span class="field-state">(optional)</span></span><input name="contains" maxlength="200" value="{{ svc.contains if svc else '' }}" placeholder='"status":"ok"'></label>
        <div class="field-pair">
          <label><span>Header name <span class="field-state">(optional, e.g. Authorization)</span></span><input name="header_name" maxlength="64" spellcheck="false" value="{{ svc.header_name if svc else '' }}"></label>
          <label><span>Header value <span class="field-state">(secret{% if svc and svc.has_header_value %} - set{% endif %})</span></span><input name="header_value" type="password" maxlength="512" autocomplete="off" placeholder="{{ 'Leave blank to keep the current value' if svc and svc.has_header_value else 'Bearer ...' }}"></label>
        </div>
        <label><span>Tags <span class="field-state">(optional, comma-separated)</span></span><input name="tags" maxlength="300" value="{{ svc.tags|join(', ') if svc else '' }}" placeholder="homelab, discord"></label>
        <div class="form-actions"><button type="submit">Add and check</button></div>
    </form>
  </details>
  {% endif %}

  {% if not sections %}
  <p class="empty-state">Nothing is being watched yet. {% if can_edit_credentials %}Use <strong>Add a service</strong> above to add the first one.{% else %}Set WEB_USERNAME and WEB_PASSWORD, then add services from this page.{% endif %}</p>
  {% endif %}

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
    {% if row.service %}
    <details class="credentials">
      <summary>Edit or remove</summary>
      {% if can_edit_credentials %}
      {% set svc = row.service %}
      <form class="credential-form" method="post" action="/services/{{ row.id }}" autocomplete="off">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <div class="field-pair">
          <label><span>Name</span><input name="name" required maxlength="60" value="{{ svc.name if svc else '' }}" placeholder="My API"></label>
          <label><span>Method</span><select name="method">{% for method in ['GET', 'HEAD', 'POST'] %}<option{% if (svc.method if svc else 'GET') == method %} selected{% endif %}>{{ method }}</option>{% endfor %}</select></label>
        </div>
        <label><span>URL</span><input name="url" type="url" required maxlength="512" spellcheck="false" value="{{ svc.url if svc else '' }}" placeholder="https://api.example.com/health"></label>
        <div class="field-pair">
          <label><span>Counts as up when the status is</span><input name="expected_status" maxlength="60" value="{{ svc.expected_status if svc else '200-399' }}" placeholder="200-399"></label>
          <label><span>Timeout (seconds)</span><input name="timeout" inputmode="numeric" maxlength="2" value="{{ svc.timeout if svc else 15 }}"></label>
        </div>
        <label><span>Response must contain <span class="field-state">(optional)</span></span><input name="contains" maxlength="200" value="{{ svc.contains if svc else '' }}" placeholder='"status":"ok"'></label>
        <div class="field-pair">
          <label><span>Header name <span class="field-state">(optional, e.g. Authorization)</span></span><input name="header_name" maxlength="64" spellcheck="false" value="{{ svc.header_name if svc else '' }}"></label>
          <label><span>Header value <span class="field-state">(secret{% if svc and svc.has_header_value %} - set{% endif %})</span></span><input name="header_value" type="password" maxlength="512" autocomplete="off" placeholder="{{ 'Leave blank to keep the current value' if svc and svc.has_header_value else 'Bearer ...' }}"></label>
        </div>
        <label><span>Tags <span class="field-state">(optional, comma-separated)</span></span><input name="tags" maxlength="300" value="{{ svc.tags|join(', ') if svc else '' }}" placeholder="homelab, discord"></label>
        <div class="form-actions"><button type="submit">Save and re-check</button></div>
      </form>
      <form class="credential-form" method="post" action="/services/{{ row.id }}/delete">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <div class="form-actions"><button type="submit" class="button-quiet">Remove this service</button><span class="hint">Removing deletes its settings and last result.</span></div>
      </form>
      {% elif auth_configured %}
      <p class="hint"><a href="/login">Sign in</a> to edit services from this page.</p>
      {% else %}
      <p class="hint">Set WEB_USERNAME and WEB_PASSWORD to edit services from this page.</p>
      {% endif %}
    </details>
    {% elif row.credentials %}
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
      {% elif auth_configured %}
      <p class="hint"><a href="/login">Sign in</a> to edit credentials from this page.</p>
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


def create_app():
    if not auth_enabled():
        print("WARNING: WEB_USERNAME/WEB_PASSWORD not set; the Run checks button is unauthenticated.", flush=True)
    setup_plugins(app)
    start_daily_schedule()
    return app


if __name__ == "__main__":
    host = os.getenv("BIND_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5002"))
    create_app().run(host=host, port=port, threaded=True)
