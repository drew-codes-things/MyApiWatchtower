import os

import requests

from checks.result import fail, not_configured, ok, redact, timed

TMDB_CONFIGURATION_URL = "https://api.themoviedb.org/3/configuration"


def run():
    api_key = os.environ.get("TMDB_API_KEY", "").strip()
    if not api_key:
        return not_configured("TMDB_API_KEY is not set")

    def do_check():
        try:
            response = requests.get(TMDB_CONFIGURATION_URL, params={"api_key": api_key}, timeout=10)
        except requests.RequestException as error:
            return fail(redact(f"Request failed: {error}", api_key))
        if response.status_code == 200:
            return ok("TMDB accepted the key and returned its configuration")
        if response.status_code == 401:
            return fail("TMDB rejected the key (401 Unauthorized)")
        return fail(f"TMDB returned unexpected status {response.status_code}")

    return timed(do_check)
