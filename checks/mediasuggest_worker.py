import requests

from checks.result import fail, ok, timed

WORKER_URL = "https://api.drew-gnr.xyz/tmdb"


def run():
    def do_check():
        try:
            response = requests.get(WORKER_URL, params={"type": "movie"}, timeout=10)
        except requests.RequestException as error:
            return fail(f"Request failed: {error}")
        if response.status_code != 200:
            return fail(f"Worker returned unexpected status {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            return fail("Worker returned 200 but the body isn't JSON")
        if body.get("error"):
            return fail(f"Worker returned an error: {body['error']}")
        return ok("Worker proxied a TMDB movie recommendation successfully")

    return timed(do_check)
