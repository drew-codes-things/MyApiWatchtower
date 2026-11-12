import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests
import responses

from checks.probe import describe_request_error
from conftest import LOGIN, csrf_token

PRIVATE_PATH = "/private/path"
QUERY_SECRET = "QUERY-SECRET-VALUE"


class PlainHttpHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def plain_http_port():
    server = HTTPServer(("127.0.0.1", 0), PlainHttpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def closed_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def private_url(port):
    return f"https://127.0.0.1:{port}{PRIVATE_PATH}?apikey={QUERY_SECRET}"


def add_service(client, url):
    return client.post("/services", headers=LOGIN, data={
        "csrf_token": csrf_token(client), "name": "Private API", "url": url, "method": "GET", "timeout": "5"})


def assert_url_is_hidden(text):
    assert PRIVATE_PATH not in text
    assert QUERY_SECRET not in text


@pytest.mark.parametrize("port_fixture, expected", [
    ("plain_http_port", "TLS/certificate error"),
    ("closed_port", "connection refused"),
])
def test_a_failing_check_never_shows_the_path_or_query(client, tmp_path, request, port_fixture, expected):
    port = request.getfixturevalue(port_fixture)
    response = add_service(client, private_url(port))
    assert "fail" in response.headers["Location"]
    assert_url_is_hidden(client.get("/").data.decode())
    [result] = json.loads((tmp_path / "results.json").read_text())["results"].values()
    assert_url_is_hidden(json.dumps(result))
    assert expected in result["message"] and "https://127.0.0.1" in result["message"]


@pytest.mark.parametrize("error_type", [
    requests.exceptions.SSLError,
    requests.exceptions.ProxyError,
    requests.exceptions.ConnectionError,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ReadTimeout,
    requests.exceptions.TooManyRedirects,
    requests.exceptions.InvalidURL,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
    requests.exceptions.RequestException,
])
def test_request_errors_become_fixed_sentences(error_type):
    url = private_url(8443)
    description = describe_request_error(error_type(f"Max retries exceeded with url: {url}"), url)
    assert_url_is_hidden(description)
    assert "Max retries" not in description


@responses.activate
def test_a_missing_text_failure_does_not_show_the_response_body(client, tmp_path):
    url = "https://private.example.test/api"
    responses.add(responses.GET, url, body="PRIVATE-BODY-START account balance 1234", status=200)
    response = client.post("/services", headers=LOGIN, data={
        "csrf_token": csrf_token(client), "name": "Private API", "url": url, "method": "GET", "contains": "healthy"})
    assert "fail" in response.headers["Location"]
    assert "PRIVATE-BODY-START" not in client.get("/").data.decode()
    [result] = json.loads((tmp_path / "results.json").read_text())["results"].values()
    assert "PRIVATE-BODY-START" not in json.dumps(result)
    assert "'healthy'" in result["message"]
