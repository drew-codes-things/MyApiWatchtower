import os

from checks.probe import Probe, StepFailure, field, get_json, http, non_empty_list, require_fields
from checks.result import not_configured, timed
from checks.settings import configured_url

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"
KNOWN_MOVIE_ID = 27205


def run():
    api_key = os.environ.get("TMDB_API_KEY", "").strip()
    if not api_key:
        return not_configured("TMDB_API_KEY is not set")

    def tmdb(path, **params):
        return get_json(f"{configured_url('TMDB_API_URL', TMDB_BASE)}{path}", params={"api_key": api_key, **params})

    def configuration():
        body = tmdb("/configuration")
        require_fields(body, "images.secure_base_url")
        return "key accepted, image configuration present"

    def drewbot_search(media_type, query, title_field, date_field):
        body = tmdb(f"/search/{media_type}", query=query, language="en-US", page=1)
        results = non_empty_list(body, "results")
        require_fields(results[0], title_field, "overview", "vote_average", "poster_path", date_field)
        return f"{len(results)} results, first is '{results[0][title_field]}'"

    def backdrop_search():
        body = tmdb("/search/multi", query="Inception", include_adult="false")
        results = [r for r in non_empty_list(body, "results") if r.get("media_type") in ("movie", "tv")]
        if not results:
            raise StepFailure("no movie/tv results - 'media_type' is missing or its values changed")
        require_fields(results[0], "id", "media_type", "vote_count", "vote_average", "genre_ids")
        return f"{len(results)} movie/tv results"

    def backdrop_images():
        body = tmdb(f"/movie/{KNOWN_MOVIE_ID}/images")
        backdrops = non_empty_list(body, "backdrops")
        require_fields(backdrops[0], "file_path", "iso_639_1", "width", "height", "vote_average")
        field(body, "posters")
        largest = max(backdrops, key=lambda b: b.get("width", 0))
        return f"{len(backdrops)} backdrops, largest {largest['width']}x{largest['height']}", largest["file_path"]

    def image_cdn(file_path):
        response = http("GET", f"{TMDB_IMAGE_BASE}/original{file_path}", stream=True)
        content_type = response.headers.get("Content-Type", "")
        response.close()
        if not content_type.startswith("image/"):
            raise StepFailure(f"returned {content_type or 'no content type'}, not an image")
        return f"served {content_type}"

    def do_check():
        probe = Probe(api_key)
        probe.step("Key and configuration (/configuration)", configuration)
        probe.step("DrewBot movie search (/search/movie)",
                   lambda: drewbot_search("movie", "Inception", "title", "release_date"))
        probe.step("DrewBot TV search (/search/tv)",
                   lambda: drewbot_search("tv", "Breaking Bad", "name", "first_air_date"))
        probe.step("TmdbBackdropDL search (/search/multi)", backdrop_search)
        file_path = probe.step("TmdbBackdropDL images (/movie/{id}/images)", backdrop_images)
        probe.step("TmdbBackdropDL download (image.tmdb.org/t/p/original)", lambda: image_cdn(file_path),
                   needs=[("a backdrop path", file_path)])
        return probe.result()

    return timed(do_check)
