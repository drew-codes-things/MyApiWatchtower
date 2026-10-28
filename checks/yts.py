from checks.probe import Probe, StepFailure, get_json, http, non_empty_list, require_fields
from checks.result import timed

YTS_LIST_MOVIES_URL = "https://movies-api.accel.li/api/v2/list_movies.json"
TORRENT_DOWNLOAD_URL = "https://yts.gg/torrent/download/{hash}"
TORRENT_FIELDS = ("hash", "quality", "size", "size_bytes", "url", "video_codec")


def _list_movies(**params):
    body = get_json(YTS_LIST_MOVIES_URL, params=params, timeout=20)
    if body.get("status") != "ok":
        raise StepFailure(f"status '{body.get('status')}': {body.get('status_message', 'no message')}")
    return body


def run():
    def rss_scan():
        # YtsPlexRss's scan: 1080p movies 50 a page, newest year first, using movie_count to work out the pages.
        body = _list_movies(quality="1080p", limit=50, page=1, sort_by="year", order="desc")
        require_fields(body, "data.movie_count", "data.limit")
        movies = non_empty_list(body, "data.movies")
        require_fields(movies[0], "id", "title", "year", "torrents")
        return f"{body['data']['movie_count']} 1080p movies, first '{movies[0]['title']}' ({movies[0]['year']})"

    def title_search():
        # YtsSearch looks a title up by query_term and reads each torrent's details.
        body = _list_movies(query_term="Inception", limit=50, page=1)
        movies = non_empty_list(body, "data.movies")
        match = next((m for m in movies if m.get("title") == "Inception"), None)
        if match is None:
            raise StepFailure("searching 'Inception' didn't return it, so title search is broken")
        torrents = non_empty_list(match, "torrents")
        require_fields(torrents[0], *TORRENT_FIELDS)
        return f"found Inception ({match['year']}) with {len(torrents)} torrents", torrents[0]["hash"]

    def torrent_download(torrent_hash):
        # Both projects download .torrent files from yts.gg by hash.
        response = http("GET", TORRENT_DOWNLOAD_URL.format(hash=torrent_hash.upper()), stream=True,
                        headers={"User-Agent": "Mozilla/5.0"})
        content_type = response.headers.get("Content-Type", "")
        first_bytes = next(response.iter_content(16), b"")
        response.close()
        # A .torrent file is bencoded and always starts with a dictionary, "d".
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
