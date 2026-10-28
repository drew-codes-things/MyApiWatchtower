from checks.probe import Probe, StepFailure, get_json, require_fields
from checks.result import timed

WORKER_URL = "https://api.drew-gnr.xyz/tmdb"
POSTER_PREFIX = "https://image.tmdb.org/"


def run():
    def recommendation(media_type, genres="", must_have_genre=None, must_be_language=None):
        params = {"type": media_type}
        if genres:
            params["genres"] = genres
        body = get_json(WORKER_URL, params=params)
        if body.get("error"):
            raise StepFailure(f"worker returned an error: {body['error']}")
        require_fields(body, "title", "year", "score", "genres", "desc", "poster", "type")
        if body["type"] != media_type:
            raise StepFailure(f"asked for {media_type} but the pick is typed '{body['type']}'")
        if body["poster"] and not str(body["poster"]).startswith(POSTER_PREFIX):
            raise StepFailure(f"poster isn't a TMDB image URL: {body['poster']}")
        if must_have_genre and must_have_genre not in body["genres"]:
            raise StepFailure(f"'{body['title']}' isn't tagged {must_have_genre} "
                              f"(genres: {', '.join(body['genres']) or 'none'}), so the worker "
                              f"isn't filtering {media_type} properly")
        if must_be_language and body.get("language") != must_be_language:
            raise StepFailure(f"'{body['title']}' has original language '{body.get('language') or 'not reported'}', "
                              f"not '{must_be_language}', so it isn't really {media_type}")
        return f"picked '{body['title']}' ({body['year'] or 'no year'})"

    def do_check():
        probe = Probe()
        probe.step("Movie pick (type=movie)", lambda: recommendation("movie"))
        probe.step("Movie pick with a genre (type=movie&genres=comedy)",
                   lambda: recommendation("movie", "comedy", must_have_genre="Comedy"))
        probe.step("TV pick (type=tv)", lambda: recommendation("tv"))
        probe.step("Anime pick (type=anime)",
                   lambda: recommendation("anime", must_have_genre="Animation", must_be_language="ja"))
        return probe.result()

    return timed(do_check)
