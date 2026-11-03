from checks.probe import Probe, StepFailure, http, non_empty_list
from checks.result import timed
from checks.settings import configured_url

ANILIST_URL = "https://graphql.anilist.co"
ANILIST_QUERY = """
query ($search: String) {
  Page(perPage: 5) {
    media(search: $search, type: ANIME, format: MOVIE) { seasonYear startDate { year } }
  }
}
"""
KNOWN_ANIME_FILM = ("Spirited Away", 2001)
KNOWN_LIVE_ACTION_FILM = ("Dune: Part Two", 2024)
YEAR_TOLERANCE = 2


def _anime_film_years(title):
    response = http("POST", configured_url("ANILIST_API_URL", ANILIST_URL), json={"query": ANILIST_QUERY, "variables": {"search": title}}, timeout=8)
    try:
        body = response.json()
    except ValueError as error:
        raise StepFailure("AniList didn't return JSON") from error
    if body.get("errors"):
        raise StepFailure(f"AniList rejected the query: {body['errors'][0].get('message', 'unknown error')}")
    media = (((body.get("data") or {}).get("Page") or {}).get("media"))
    if media is None:
        raise StepFailure("field 'data.Page.media' is missing (response shape changed)")
    return [(entry.get("startDate") or {}).get("year") or entry.get("seasonYear") for entry in media]


def _matches(years, year):
    return any(y and abs(int(y) - year) <= YEAR_TOLERANCE for y in years)


def run():
    def anime_search():
        title, year = KNOWN_ANIME_FILM
        years = _anime_film_years(title)
        non_empty_list({"media": years}, "media")
        if not _matches(years, year):
            raise StepFailure(f"'{title}' didn't come back as a {year} anime film, so YtsPlexRss would stop "
                              "filtering out anime films")
        return f"'{title}' matched as a {year} anime film, so the anime filter would recognise it"

    def live_action_search():
        title, year = KNOWN_LIVE_ACTION_FILM
        if _matches(_anime_film_years(title), year):
            raise StepFailure(f"'{title}' matched as an anime film, so YtsPlexRss would wrongly hide it")
        return f"'{title}' isn't an anime film, so it stays in YtsPlexRss's list"

    def do_check():
        probe = Probe()
        probe.step("YtsPlexRss anime filter (AniList, anime film)", anime_search)
        probe.step("YtsPlexRss anime filter (AniList, live-action film)", live_action_search)
        return probe.result()

    return timed(do_check)
