import requests

from checks.result import fail, ok, timed

YTS_LIST_MOVIES_URL = "https://movies-api.accel.li/api/v2/list_movies.json"


def run():
    def do_check():
        try:
            response = requests.get(YTS_LIST_MOVIES_URL, params={"limit": 1}, timeout=10)
        except requests.RequestException as error:
            return fail(f"Request failed: {error}")
        if response.status_code != 200:
            return fail(f"YTS returned unexpected status {response.status_code}")
        body = response.json()
        if body.get("status") != "ok":
            return fail("YTS returned 200 but the response shape has changed")
        return ok("YTS responded with the expected shape")

    return timed(do_check)
