import json
import stat

import pytest
import responses

import registry
import services_store
from conftest import LOGIN, csrf_token

API = "https://status.example.test/health"


def add(client, **fields):
    data = {"csrf_token": csrf_token(client), "name": "Status page", "url": API, "method": "GET", **fields}
    return client.post("/services", headers=LOGIN, data=data)


def saved(tmp_path):
    return json.loads((tmp_path / "services.json").read_text())


@responses.activate
def test_a_service_can_be_added_and_is_checked_straight_away(client, tmp_path):
    responses.add(responses.GET, API, body='{"status":"ok"}', status=200)
    response = add(client, contains='"status":"ok"', tags="homelab, api")
    assert response.status_code == 303 and "checked%20it%3A%20ok" in response.headers["Location"]
    [service] = saved(tmp_path)
    assert service["name"] == "Status page" and service["tags"] == ["homelab", "api"]
    assert stat.S_IMODE((tmp_path / "services.json").stat().st_mode) == 0o600
    page = client.get("/", headers=LOGIN).data.decode()
    assert "Your services" in page and "Status page" in page and f'action="/services/{service["id"]}/delete"' in page


@responses.activate
def test_a_wrong_status_or_missing_text_fails_the_check(client, tmp_path):
    responses.add(responses.GET, API, body="maintenance", status=503)
    assert "fail" in add(client).headers["Location"]
    responses.replace(responses.GET, API, body="maintenance", status=200)
    assert "fail" in add(client, name="Second", contains="ok-status").headers["Location"]


@responses.activate
def test_the_secret_header_is_sent_but_never_shown(client, tmp_path):
    responses.add(responses.GET, API, status=200)
    add(client, header_name="Authorization", header_value="Bearer hidden-value")
    assert responses.calls[0].request.headers["Authorization"] == "Bearer hidden-value"
    page = client.get("/", headers=LOGIN).data.decode()
    assert "hidden-value" not in page and "secret - set" in page


@responses.activate
def test_editing_keeps_the_secret_when_left_blank(client, tmp_path):
    responses.add(responses.GET, API, status=200)
    add(client, header_name="X-Key", header_value="keep-me")
    service_id = saved(tmp_path)[0]["id"]
    client.post(f"/services/{service_id}", headers=LOGIN, data={
        "csrf_token": csrf_token(client), "name": "Renamed", "url": API, "method": "GET", "header_name": "X-Key", "header_value": ""})
    [service] = saved(tmp_path)
    assert service["name"] == "Renamed" and service["header_value"] == "keep-me" and service["id"] == service_id


@responses.activate
def test_a_service_can_be_removed_with_its_result(client, tmp_path):
    responses.add(responses.GET, API, status=200)
    add(client)
    service_id = saved(tmp_path)[0]["id"]
    response = client.post(f"/services/{service_id}/delete", headers=LOGIN, data={"csrf_token": csrf_token(client)})
    assert response.status_code == 303
    assert saved(tmp_path) == []
    assert service_id not in json.loads((tmp_path / "results.json").read_text())["results"]
    assert client.post(f"/services/{service_id}/delete", headers=LOGIN, data={"csrf_token": csrf_token(client)}).status_code == 404


@pytest.mark.parametrize("fields, message", [
    ({"name": ""}, "Give%20the%20service%20a%20name"),
    ({"url": "not a link"}, "full%20http"),
    ({"method": "DELETE"}, "Method%20must"),
    ({"expected_status": "2xx"}, "isn%27t%20a%20status"),
    ({"timeout": "0"}, "Timeout"),
    ({"header_name": "Bad Header"}, "Header%20name"),
    ({"header_value": "orphan"}, "Add%20a%20header%20name"),
    ({"method": "HEAD", "contains": "x"}, "HEAD%20request"),
])
def test_bad_service_details_are_refused(client, tmp_path, fields, message):
    response = add(client, **fields)
    assert message in response.headers["Location"]
    assert not (tmp_path / "services.json").exists()


def test_services_need_the_login(client, monkeypatch):
    assert client.post("/services", data={"csrf_token": csrf_token(client), "name": "x", "url": API}).status_code == 401
    monkeypatch.setattr("app.WEB_USERNAME", "")
    assert client.post("/services", data={"csrf_token": csrf_token(client), "name": "x", "url": API}).status_code == 403


def test_a_fresh_install_watches_nothing(client, monkeypatch):
    monkeypatch.setattr(registry, "_plugins", [])
    page = client.get("/").data.decode()
    assert "Nothing is being watched yet" in page


def test_status_ranges_are_parsed():
    assert services_store.expected_status_codes("200, 301-302") == {200, 301, 302}
    with pytest.raises(services_store.InvalidService):
        services_store.expected_status_codes("99")


def test_plugins_load_from_a_folder(tmp_path, monkeypatch):
    (tmp_path / "my_checks.py").write_text('CHECKS = [{"id": "x", "label": "X", "projects": [], "kind": "api", "run": None}]\n')
    (tmp_path / "_ignored.py").write_text("raise SystemExit\n")
    monkeypatch.setattr(registry, "_plugins", None)
    plugins = registry.load_plugins(str(tmp_path))
    assert [plugin.__name__ for plugin in plugins] == ["my_checks"]
    assert registry.plugin_checks()[0]["id"] == "x"


def test_alerts_only_mention_checks_that_changed(monkeypatch):
    import alerts
    previous = {"a": {"status": "ok"}, "b": {"status": "fail"}, "c": {"status": "fail"}, "d": {"status": "ok"}}
    current = {"a": {"status": "fail", "message": "HTTP 500"}, "b": {"status": "ok", "message": ""},
               "c": {"status": "fail", "message": "still"}, "d": {"status": "ok", "message": ""}, "e": {"status": "fail", "message": "new"}}
    assert alerts.status_changes(previous, current, {"a": "API A", "b": "API B"}) == [
        "🔴 API A is failing: HTTP 500", "🟢 API B is working again"]


@responses.activate
def test_alerts_go_to_the_discord_webhook_when_set(monkeypatch):
    import alerts
    hook = "https://discord.example.test/api/webhooks/1/abc"
    responses.add(responses.POST, hook, status=204)
    monkeypatch.delenv("DISCORD_ALERT_WEBHOOK_URL", raising=False)
    assert alerts.send_alert(["x"]) is False
    monkeypatch.setenv("DISCORD_ALERT_WEBHOOK_URL", hook)
    assert alerts.send_alert(["🔴 API A is failing"]) is True
    assert json.loads(responses.calls[0].request.body)["content"].endswith("🔴 API A is failing")


@responses.activate
def test_anonymous_viewers_see_status_but_not_urls(client, tmp_path):
    responses.add(responses.GET, API, status=200)
    add(client)
    page = client.get("/").data.decode()
    assert "Status page" in page and 'href="/login"' in page
    assert API not in page and 'action="/services"' not in page
    assert client.get("/login").status_code == 401
    assert client.get("/login", headers=LOGIN).status_code == 302
    signed_in = client.get("/", headers=LOGIN).data.decode()
    assert API in signed_in and "Sign in to edit" not in signed_in
