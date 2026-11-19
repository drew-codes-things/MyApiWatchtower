import base64
import ipaddress
import os
import shutil
import stat
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app as watchtower
import credentials_store
import registry
import results_store
import services_store
from conftest import DEMO_URL, csrf_token, demo_plugin
from signin import SESSION_SECONDS, FailureLimiter, OwnerAccount

REPO = os.path.join(os.path.dirname(__file__), "..")
COOKIE = "watchtower_session"
OWNER_NAME = "owner"
PASSWORD = "correct horse battery"
HEADER = "X-Forwarded-User"
SERVICE = {"name": "Status API", "url": "https://status.example.test/health"}
BASIC = {"Authorization": "Basic " + base64.b64encode(b"basic-user:a long basic password").decode()}


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def runs(monkeypatch):
    started = []
    monkeypatch.setattr(watchtower, "start_run", started.append)
    return started


@pytest.fixture
def web(tmp_path, monkeypatch, clock, runs):
    monkeypatch.setattr(credentials_store, "CREDENTIALS_PATH", str(tmp_path / "credentials.json"))
    monkeypatch.setattr(results_store, "RESULTS_PATH", str(tmp_path / "results.json"))
    monkeypatch.setattr(services_store, "SERVICES_PATH", str(tmp_path / "services.json"))
    monkeypatch.setattr(registry, "_plugins", [demo_plugin()])
    monkeypatch.setattr(watchtower, "WEB_USERNAME", "")
    monkeypatch.setattr(watchtower, "WEB_PASSWORD", "")
    monkeypatch.setattr(watchtower, "ALLOW_INSECURE", False)
    monkeypatch.setattr(watchtower.OWN_SIGN_IN, "owner", OwnerAccount(tmp_path / "owner.json"))
    monkeypatch.setattr(watchtower.OWN_SIGN_IN, "limiter", FailureLimiter(clock=clock))
    monkeypatch.setattr(watchtower.OWN_SIGN_IN, "device_limiter", FailureLimiter(clock=clock, before_strict=None))
    monkeypatch.setattr(watchtower.OWN_SIGN_IN, "clock", clock)
    monkeypatch.setattr(watchtower.OWN_SIGN_IN, "_setup_code", None)
    monkeypatch.delenv("DEMO_API_KEY", raising=False)
    monkeypatch.delenv("DEMO_API_URL", raising=False)
    return watchtower.app.test_client()


def set_up_owner(web, form=None, **kwargs):
    details = {"code": watchtower.OWN_SIGN_IN.setup_code(), "user": OWNER_NAME, "password": PASSWORD, "confirm": PASSWORD}
    details.update(form or {})
    return web.post("/setup", data=details, **kwargs)


def sign_in(web, password=PASSWORD, **kwargs):
    return web.post("/signin", data={"user": OWNER_NAME, "password": password}, **kwargs)


def forget_the_browser(web) -> None:
    web.delete_cookie(COOKIE)
    web.delete_cookie(COOKIE + "_device", path="/signin")


def session_cookie(response) -> str:
    return next(value for value in response.headers.getlist("Set-Cookie") if value.startswith(COOKIE + "="))


def add_service(web, **headers):
    return web.post("/services", data={"csrf_token": csrf_token(web), **SERVICE}, headers=headers)


def run_checks(web, **headers):
    return web.post("/run-checks", data={"csrf_token": csrf_token(web)}, headers=headers)


def test_the_own_sign_in_is_the_default(web):
    assert watchtower.sign_in_method() == "builtin"


def test_the_status_page_stays_public_without_links(web):
    page = web.get("/")
    assert page.status_code == 200
    assert "Demo API" in page.data.decode()
    assert DEMO_URL not in page.data.decode()
    assert 'href="/login"' in page.data.decode()


def test_signing_in_starts_at_setup_then_at_sign_in(web):
    assert web.get("/login").headers["Location"].endswith("/setup")
    set_up_owner(web)
    web.delete_cookie(COOKIE)
    assert web.get("/login").headers["Location"].endswith("/signin")


def test_running_and_editing_need_a_sign_in(web, tmp_path, runs):
    assert run_checks(web).status_code == 401
    assert add_service(web).status_code == 401
    assert web.post("/credentials/demo", data={"csrf_token": csrf_token(web), "DEMO_API_KEY": "x"}).status_code == 401
    assert runs == []
    assert not (tmp_path / "services.json").exists()
    assert not (tmp_path / "credentials.json").exists()


def test_a_signed_out_viewer_gets_no_run_button(web):
    assert 'id="run-form"' not in web.get("/").data.decode()
    set_up_owner(web)
    assert 'id="run-form"' in web.get("/").data.decode()


def test_the_owner_runs_checks_and_edits_after_setup(web, tmp_path, runs):
    assert set_up_owner(web).status_code == 303
    assert stat.S_IMODE(os.stat(tmp_path / "owner.json").st_mode) == 0o600
    page = web.get("/").data.decode()
    assert DEMO_URL in page and 'href="/signout"' in page and 'href="/login"' not in page
    assert run_checks(web).status_code == 204
    assert runs == ["manual"]
    assert add_service(web).status_code == 303
    assert (tmp_path / "services.json").exists()


def test_setup_needs_the_code_and_happens_once(web):
    assert set_up_owner(web, form={"code": "not-the-code"}).status_code == 401
    assert set_up_owner(web).status_code == 303
    assert set_up_owner(web, form={"user": "intruder"}).headers["Location"].endswith("/signin")
    assert watchtower.OWN_SIGN_IN.owner.read()["user"] == OWNER_NAME


def test_the_user_header_is_ignored(web, runs):
    set_up_owner(web)
    web.delete_cookie(COOKIE)
    assert run_checks(web, **{HEADER: OWNER_NAME}).status_code == 401
    assert runs == []


def test_wrong_passwords_are_rate_limited(web):
    set_up_owner(web)
    forget_the_browser(web)
    for _attempt in range(5):
        assert sign_in(web, password="wrong password!").status_code == 401
    assert sign_in(web).status_code == 429
    assert sign_in(web, environ_base={"REMOTE_ADDR": "192.0.2.7"}).status_code == 303


def test_signing_out_and_expiry_end_the_session(web, clock):
    set_up_owner(web)
    assert run_checks(web).status_code == 204
    clock.now += SESSION_SECONDS + 1
    assert run_checks(web).status_code == 401
    clock.now -= SESSION_SECONDS + 1
    assert run_checks(web).status_code == 204
    assert web.post("/signout").status_code == 303
    assert run_checks(web).status_code == 401


@pytest.mark.parametrize("token", ["é.é", "abc.\udcff", "no-dot", "", None])
def test_an_odd_session_value_is_refused_not_crashed(web, token):
    assert watchtower.OWN_SIGN_IN.user_for_token(token) is None


def test_the_session_cookie_flags(web):
    local = set_up_owner(web)
    assert "HttpOnly" in session_cookie(local) and "SameSite=Lax" in session_cookie(local)
    assert "Secure" not in session_cookie(local)
    remote = sign_in(web, base_url="http://192.0.2.1")
    assert "Secure" in session_cookie(remote)


def test_the_no_sign_in_setting_leaves_it_to_a_proxy(web, monkeypatch, runs):
    monkeypatch.setattr(watchtower, "ALLOW_INSECURE", True)
    assert watchtower.sign_in_method() == "proxy"
    assert DEMO_URL in web.get("/").data.decode()
    assert run_checks(web).status_code == 204
    assert add_service(web).status_code == 303
    assert web.get("/setup").status_code == 404
    assert web.get("/signin").status_code == 404


def test_basic_credentials_still_work_as_before(web, monkeypatch, runs):
    monkeypatch.setattr(watchtower, "WEB_USERNAME", "basic-user")
    monkeypatch.setattr(watchtower, "WEB_PASSWORD", "a long basic password")
    assert watchtower.sign_in_method() == "basic"
    page = web.get("/").data.decode()
    assert 'id="run-form"' in page and 'href="/login"' in page and DEMO_URL not in page
    assert web.get("/login").status_code == 401
    assert run_checks(web).status_code == 401
    assert run_checks(web, **BASIC).status_code == 204
    assert DEMO_URL in web.get("/", headers=BASIC).data.decode()
    assert web.get("/setup").status_code == 404


def import_app(tmp_path, shown="app.OWN_SIGN_IN.secret.decode()", **settings):
    copy = tmp_path / "app"
    if not copy.exists():
        shutil.copytree(REPO, copy, ignore=shutil.ignore_patterns("tests", ".git", "__pycache__", "*.json", ".env",
                                                                    "secret_key", "plugins"))
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}
    env.update(settings)
    return subprocess.run([sys.executable, "-c", f"import app; print({shown})"], cwd=copy, env=env, capture_output=True,
                          text=True, timeout=60)


def test_the_sign_in_key_is_saved_and_reused_when_imported(tmp_path):
    first = import_app(tmp_path)
    second = import_app(tmp_path)
    assert first.returncode == 0, first.stderr
    key_file = tmp_path / "app" / "secret_key"
    assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
    assert first.stdout.strip() == second.stdout.strip() == key_file.read_text()


@pytest.mark.parametrize("settings", [{"SECRET_KEY": "a"}, {"SECRET_KEY": "y" * 31}])
def test_a_short_sign_in_key_is_refused(tmp_path, settings):
    started = import_app(tmp_path, **settings)
    assert started.returncode != 0
    assert "SECRET_KEY holds" in started.stderr


def test_a_short_key_file_is_refused(tmp_path):
    import_app(tmp_path, SECRET_KEY="z" * 32)
    (tmp_path / "app" / "secret_key").write_bytes(b"short")
    started = import_app(tmp_path)
    assert started.returncode != 0
    assert "fewer than the 32" in started.stderr


def test_the_proxy_and_basic_modes_need_no_key_file_and_take_any_key(tmp_path):
    assert import_app(tmp_path, ALLOW_INSECURE="true", SECRET_KEY="a", TRUSTED_PROXIES="0.0.0.0/0").returncode == 0
    assert import_app(tmp_path, WEB_USERNAME="basic-user", WEB_PASSWORD="a long basic password").returncode == 0
    assert not (tmp_path / "app" / "secret_key").exists()


def test_allowed_hosts_and_trusted_proxies_come_from_the_environment(tmp_path):
    shown = "app.OWN_SIGN_IN.accepts_host('localhost'), app.OWN_SIGN_IN.accepts_host('watch.example.com'), " \
            "app.OWN_SIGN_IN.client_address('192.0.2.10', {'X-Forwarded-For': '203.0.113.9'})"
    started = import_app(tmp_path, shown, ALLOWED_HOSTS="watch.example.com", TRUSTED_PROXIES="192.0.2.10")
    assert started.returncode == 0, started.stderr
    assert started.stdout.split() == ["False", "True", "203.0.113.9"]
    assert import_app(tmp_path, shown).stdout.split() == ["True", "False", "192.0.2.10"]


@pytest.mark.parametrize("value", ["0.0.0.0/0", "::/0", "10.0.0.5/8", "not-an-address"])
def test_trusted_proxies_that_reach_too_far_are_refused(tmp_path, value):
    started = import_app(tmp_path, TRUSTED_PROXIES=value)
    assert started.returncode != 0
    assert "TRUSTED_PROXIES" in started.stderr


def test_a_rebinding_host_name_gets_421(web):
    set_up_owner(web)
    attacker = watchtower.app.test_client()
    for _attempt in range(6):
        assert sign_in(attacker, password="wrong password!", base_url="http://rebind.evil.example:5002").status_code == 421
    assert attacker.get("/", base_url="http://rebind.evil.example:5002").status_code == 421
    assert sign_in(attacker).status_code == 303


def test_the_proxy_and_basic_modes_answer_on_any_host_name(web, monkeypatch):
    monkeypatch.setattr(watchtower, "ALLOW_INSECURE", True)
    assert web.get("/", base_url="http://watch.example.com").status_code == 200
    monkeypatch.setattr(watchtower, "WEB_USERNAME", "basic-user")
    monkeypatch.setattr(watchtower, "WEB_PASSWORD", "a long basic password")
    assert web.get("/", base_url="http://watch.example.com").status_code == 200


def test_a_browser_that_signed_in_before_is_not_locked_out_by_others(web):
    set_up_owner(web)
    assert web.post("/signout").status_code == 303
    attacker = watchtower.app.test_client()
    for _attempt in range(5):
        assert sign_in(attacker, password="wrong password!").status_code == 401
    assert sign_in(attacker).status_code == 429
    assert sign_in(web).status_code == 303


def test_the_forwarded_address_counts_only_from_a_trusted_proxy(web, monkeypatch):
    monkeypatch.setattr(watchtower.OWN_SIGN_IN, "trusted_proxies", (ipaddress.ip_network("192.0.2.10"),))
    set_up_owner(web)
    via_proxy = {"REMOTE_ADDR": "192.0.2.10"}
    attacker = watchtower.app.test_client()
    for _attempt in range(5):
        sign_in(attacker, password="wrong password!", environ_base=via_proxy, headers={"X-Forwarded-For": "203.0.113.9"})
    assert sign_in(attacker, environ_base=via_proxy, headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 429
    owner = watchtower.app.test_client()
    assert sign_in(owner, environ_base=via_proxy, headers={"X-Forwarded-For": "198.51.100.4"}).status_code == 303
