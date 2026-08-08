import requests

from checks.result import fail, ok, timed


def run(url, label):
    def do_check():
        try:
            response = requests.get(url, timeout=10, headers={"User-Agent": "MyApiWatchtower/1.0"})
        except requests.RequestException as error:
            return fail(f"Request failed: {error}")
        if 200 <= response.status_code < 300:
            return ok(f"{label} responded with {response.status_code} (reachability only, not a real API check)")
        return fail(f"{label} returned unexpected status {response.status_code}")

    return timed(do_check)
