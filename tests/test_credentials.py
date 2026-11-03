import base64
import json
import os
import stat
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app as watchtower
import credentials_store
import results_store
from checks.result import CheckResult

LOGIN = {"Authorization": "Basic " + base64.b64encode(b"drew:secret-pass").decode()}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials_store, "CREDENTIALS_PATH", str(tmp_path / "credentials.json"))
    monkeypatch.setattr(results_store, "RESULTS_PATH", str(tmp_path / "results.json"))
    monkeypatch.setattr(watchtower, "WEB_USERNAME", "drew")
    monkeypatch.setattr(watchtower, "WEB_PASSWORD", "secret-pass")
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    tmdb = watchtower.get_check("tmdb")
    monkeypatch.setitem(tmdb, "run", lambda: CheckResult("ok", "TMDB answered"))
    watchtower.app.config["TESTING"] = True
    with watchtower.app.test_client() as test_client:
        yield test_client


def csrf_token(client):
    client.get("/")
    with client.session_transaction() as session:
        return session["_csrf_token"]


def test_saving_a_key_stores_it_privately_and_reruns_the_check(client, tmp_path):
    response = client.post("/credentials/tmdb", headers=LOGIN,
                           data={"csrf_token": csrf_token(client), "TMDB_API_KEY": "  new-key  "})
    assert response.status_code == 303 and "re-ran" in response.headers["Location"]
    saved = tmp_path / "credentials.json"
    assert json.loads(saved.read_text()) == {"TMDB_API_KEY": "new-key"}
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert os.environ["TMDB_API_KEY"] == "new-key"
    assert json.loads((tmp_path / "results.json").read_text())["results"]["tmdb"]["status"] == "ok"


def test_blank_fields_keep_the_current_value(client, tmp_path, monkeypatch):
    monkeypatch.setenv("TMDB_API_KEY", "old-key")
    response = client.post("/credentials/tmdb", headers=LOGIN, data={"csrf_token": csrf_token(client), "TMDB_API_KEY": " "})
    assert response.status_code == 303 and "Nothing%20changed" in response.headers["Location"]
    assert os.environ["TMDB_API_KEY"] == "old-key"
    assert not (tmp_path / "credentials.json").exists()


def test_only_the_checks_own_fields_are_saved(client, tmp_path):
    client.post("/credentials/tmdb", headers=LOGIN,
                data={"csrf_token": csrf_token(client), "TMDB_API_KEY": "key", "WEB_PASSWORD": "hijack"})
    assert json.loads((tmp_path / "credentials.json").read_text()) == {"TMDB_API_KEY": "key"}


def test_control_characters_are_refused(client, tmp_path):
    response = client.post("/credentials/tmdb", headers=LOGIN,
                           data={"csrf_token": csrf_token(client), "TMDB_API_KEY": "key\nEVIL=1"})
    assert response.status_code == 303 and "control%20characters" in response.headers["Location"]
    assert not (tmp_path / "credentials.json").exists()


def test_editing_needs_the_login_and_a_csrf_token(client):
    token = csrf_token(client)
    assert client.post("/credentials/tmdb", data={"csrf_token": token, "TMDB_API_KEY": "x"}).status_code == 401
    assert client.post("/credentials/tmdb", headers=LOGIN, data={"csrf_token": "wrong", "TMDB_API_KEY": "x"}).status_code == 403


def test_editing_is_refused_when_no_login_is_configured(client, monkeypatch):
    monkeypatch.setattr(watchtower, "WEB_USERNAME", "")
    response = client.post("/credentials/tmdb", data={"csrf_token": csrf_token(client), "TMDB_API_KEY": "x"})
    assert response.status_code == 403
    assert b"Set WEB_USERNAME and WEB_PASSWORD" in client.get("/").data


def test_unknown_checks_are_refused(client):
    assert client.post("/credentials/not-a-check", headers=LOGIN, data={"csrf_token": csrf_token(client)}).status_code == 404


def test_secrets_are_never_written_into_the_page(client, monkeypatch):
    monkeypatch.setenv("TMDB_API_KEY", "very-secret-value")
    page = client.get("/").data.decode()
    assert "very-secret-value" not in page and "TMDB_API_KEY - set" in page


def test_saved_credentials_override_the_environment_at_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials_store, "CREDENTIALS_PATH", str(tmp_path / "credentials.json"))
    (tmp_path / "credentials.json").write_text(json.dumps({"PLEX_SERVER_NAME": "from-page"}))
    monkeypatch.setenv("PLEX_SERVER_NAME", "from-stack")
    assert credentials_store.apply_saved_credentials() == ["PLEX_SERVER_NAME"]
    assert os.environ["PLEX_SERVER_NAME"] == "from-page"


def test_an_api_link_can_be_changed_and_is_used_by_the_check(client, tmp_path, monkeypatch):
    monkeypatch.delenv("ANILIST_API_URL", raising=False)
    calls = []
    import checks.anilist as anilist_check
    monkeypatch.setattr(anilist_check, "http", lambda method, url, **kwargs: calls.append(url) or (_ for _ in ()).throw(RuntimeError("stop")))
    response = client.post("/credentials/anilist", headers=LOGIN,
                           data={"csrf_token": csrf_token(client), "ANILIST_API_URL": "https://anilist.example.test/graphql/"})
    assert response.status_code == 303
    assert json.loads((tmp_path / "credentials.json").read_text()) == {"ANILIST_API_URL": "https://anilist.example.test/graphql/"}
    assert calls and calls[0] == "https://anilist.example.test/graphql"


def test_an_unchanged_link_is_not_saved(client, tmp_path, monkeypatch):
    monkeypatch.delenv("ANILIST_API_URL", raising=False)
    import checks.anilist as anilist_check
    response = client.post("/credentials/anilist", headers=LOGIN,
                           data={"csrf_token": csrf_token(client), "ANILIST_API_URL": anilist_check.ANILIST_URL})
    assert "Nothing%20changed" in response.headers["Location"]
    assert not (tmp_path / "credentials.json").exists()


@pytest.mark.parametrize("link", ["ftp://example.com", "example.com/api", "javascript:alert(1)", "https://exa mple.com"])
def test_bad_links_are_refused(client, tmp_path, link):
    response = client.post("/credentials/anilist", headers=LOGIN, data={"csrf_token": csrf_token(client), "ANILIST_API_URL": link})
    assert "full%20http" in response.headers["Location"]
    assert not (tmp_path / "credentials.json").exists()


def test_link_only_checks_say_so_and_show_the_current_link(client, monkeypatch):
    monkeypatch.delenv("YTS_API_URL", raising=False)
    page = client.get("/").data.decode()
    assert "Edit API link" in page and 'value="https://movies-api.accel.li/api/v2/list_movies.json"' in page
