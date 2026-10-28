import time

from checks.probe import Probe, StepFailure, get_json, http, non_empty_list, require_fields
from checks.result import timed

YTS_LIST_MOVIES_URL = "https://movies-api.accel.li/api/v2/list_movies.json"
YTS_MIRROR_URL = "https://yts.gg/api/v2/list_movies.json"
RETRY_WAITS = (10, 30)
TORRENT_DOWNLOAD_URL = "https://yts.gg/torrent/download/{hash}"
TORRENT_FIELDS = ("hash", "quality", "size", "size_bytes", "url", "video_codec")


def _list_movies(**params):
    errors = []
    for attempt in range(len(RETRY_WAITS) + 1):
        for url in (YTS_LIST_MOVIES_URL, YTS_MIRROR_URL):
            host = url.split("/")[2]
            try:
                body = get_json(url, params=params, timeout=20)
            except StepFailure as error:
                errors.append(f"{host}: {error}")
                continue
            if body.get("status") != "ok":
                errors.append(f"{host}: status '{body.get('status')}': {body.get('status_message', 'no message')}")
                continue
            return body, errors
        if attempt < len(RETRY_WAITS):
            time.sleep(RETRY_WAITS[attempt])
    raise StepFailure(f"both hosts failed {len(RETRY_WAITS) + 1} times, last: {'; '.join(errors[-2:])}")


def _retry_note(errors):
    return f" (after {len(errors)} failed tries, first: {errors[0]})" if errors else ""


def run():
    def rss_scan():
        body, errors = _list_movies(quality="1080p", limit=50, page=1, sort_by="year", order="desc")
        require_fields(body, "data.movie_count", "data.limit")
        movies = non_empty_list(body, "data.movies")
        require_fields(movies[0], "id", "title", "year", "torrents")
        return (f"{body['data']['movie_count']} 1080p movies, first '{movies[0]['title']}' ({movies[0]['year']})"
                + _retry_note(errors))

    def title_search():
        body, errors = _list_movies(query_term="Inception", limit=50, page=1)
        movies = non_empty_list(body, "data.movies")
        match = next((m for m in movies if m.get("title") == "Inception"), None)
        if match is None:
            raise StepFailure("searching 'Inception' didn't return it, so title search is broken")
        torrents = non_empty_list(match, "torrents")
        require_fields(torrents[0], *TORRENT_FIELDS)
        return (f"found Inception ({match['year']}) with {len(torrents)} torrents" + _retry_note(errors),
                torrents[0]["hash"])

    def torrent_download(torrent_hash):
        response = http("GET", TORRENT_DOWNLOAD_URL.format(hash=torrent_hash.upper()), stream=True,
                        headers={"User-Agent": "Mozilla/5.0"})
        content_type = response.headers.get("Content-Type", "")
        first_bytes = next(response.iter_content(16), b"")
        response.close()
        if not first_bytes.startswith(b"d"):
            raise StepFailure(f"got {content_type or 'unknown content'} instead of a .torrent file")
        return f"served a .torrent ({content_type or 'no content type'})"

    def do_check():
        probe = Probe()
        probe.step("YtsPlexRss new-release scan (list_movies, quality=1080p)", rss_scan)
        torrent_hash = probe.step("YtsSearch title search (list_movies, query_term)", title_search)
        probe.step("Torrent download (yts.gg/torrent/download)", lambda: torrent_download(torrent_hash),
                   needs=[("a torrent hash", torrent_hash)])
        return probe.result()

    return timed(do_check)
