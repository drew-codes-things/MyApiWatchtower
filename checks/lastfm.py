import os

import requests

from checks.result import fail, not_configured, ok, redact, timed

LASTFM_API_URL = "http://ws.audioscrobbler.com/2.0/"


def run():
    api_key = os.environ.get("LASTFM_API_KEY", "").strip()
    if not api_key:
        return not_configured("LASTFM_API_KEY is not set")

    def do_check():
        params = {"method": "chart.gettopartists", "api_key": api_key, "format": "json", "limit": 1}
        try:
            response = requests.get(LASTFM_API_URL, params=params, timeout=10)
        except requests.RequestException as error:
            return fail(redact(f"Request failed: {error}", api_key))
        if response.status_code != 200:
            return fail(f"Last.fm returned unexpected status {response.status_code}")
        body = response.json()
        if "error" in body:
            return fail(f"Last.fm returned an error: {body.get('message', body['error'])}")
        return ok("Last.fm accepted the key and returned chart data")

    return timed(do_check)
