from checks.probe import Probe, StepFailure, get_json, non_empty_list, require_fields
from checks.result import timed

JIKAN_ANIME_SEARCH_URL = "https://api.jikan.moe/v4/anime"
# YtsPlexRss's MAL filter asks "is this YTS movie an anime film?" by title and year.
# Spirited Away (2001) must come back as a Movie within two years, exactly as it checks.
KNOWN_ANIME_FILM = ("Spirited Away", 2001)
YEAR_TOLERANCE = 2


def _entry_year(entry):
    return entry.get("year") or ((entry.get("aired") or {}).get("from") or "")[:4]


def run():
    def anime_search():
        title, year = KNOWN_ANIME_FILM
        body = get_json(JIKAN_ANIME_SEARCH_URL, params={"q": title, "limit": 5}, timeout=8)
        entries = non_empty_list(body, "data")
        require_fields(entries[0], "type", "aired.from")
        for entry in entries:
            if entry.get("type") != "Movie":
                continue
            try:
                if abs(int(_entry_year(entry)) - year) <= YEAR_TOLERANCE:
                    return f"'{title}' matched as a {year} Movie, so the MAL filter would recognise it"
            except (TypeError, ValueError):
                continue
        raise StepFailure(f"'{title}' didn't come back as a {year} Movie, so YtsPlexRss would stop "
                          "filtering out anime films")

    def do_check():
        probe = Probe()
        probe.step("YtsPlexRss MAL filter (/anime?q=)", anime_search)
        return probe.result()

    return timed(do_check)
