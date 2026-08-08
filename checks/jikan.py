import requests

from checks.result import fail, ok, timed

JIKAN_ANIME_URL = "https://api.jikan.moe/v4/anime/1"


def run():
    def do_check():
        try:
            response = requests.get(JIKAN_ANIME_URL, timeout=10)
        except requests.RequestException as error:
            return fail(f"Request failed: {error}")
        if response.status_code != 200:
            return fail(f"Jikan returned unexpected status {response.status_code}")
        body = response.json()
        if "data" not in body:
            return fail("Jikan returned 200 but the response shape has changed (no 'data' key)")
        return ok("Jikan responded with the expected shape")

    return timed(do_check)
