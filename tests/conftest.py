import base64
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app as watchtower
import credentials_store
import registry
import results_store
import services_store
from checks.result import CheckResult
from checks.settings import configured_url

DEMO_URL = "https://demo.example.test/api"
requested = []


def demo_run():
    requested.append(configured_url("DEMO_API_URL", DEMO_URL))
    return CheckResult("ok", "demo answered")


def demo_plugin():
    plugin = types.ModuleType("demo_plugin")
    plugin.CHECKS = [
        {"id": "demo", "label": "Demo API", "projects": ["Demo"], "kind": "api", "run": demo_run,
         "credentials": [
             {"env": "DEMO_API_KEY", "label": "API key", "secret": True},
             {"env": "DEMO_API_URL", "label": "API link", "secret": False, "url": True, "default": DEMO_URL},
         ]},
        {"id": "plain", "label": "Plain", "projects": [], "kind": "scraper", "run": lambda: CheckResult("ok", "fine")},
    ]
    return plugin

LOGIN = {"Authorization": "Basic " + base64.b64encode(b"drew:secret-pass").decode()}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials_store, "CREDENTIALS_PATH", str(tmp_path / "credentials.json"))
    monkeypatch.setattr(results_store, "RESULTS_PATH", str(tmp_path / "results.json"))
    monkeypatch.setattr(services_store, "SERVICES_PATH", str(tmp_path / "services.json"))
    monkeypatch.setattr(registry, "_plugins", [demo_plugin()])
    monkeypatch.setattr(watchtower, "WEB_USERNAME", "drew")
    monkeypatch.setattr(watchtower, "WEB_PASSWORD", "secret-pass")
    monkeypatch.delenv("DEMO_API_KEY", raising=False)
    monkeypatch.delenv("DEMO_API_URL", raising=False)
    requested.clear()
    watchtower.app.config["TESTING"] = True
    with watchtower.app.test_client() as test_client:
        yield test_client


def csrf_token(client):
    client.get("/")
    with client.session_transaction() as session:
        return session["_csrf_token"]
