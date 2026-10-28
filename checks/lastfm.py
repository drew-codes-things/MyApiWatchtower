import os

from checks.probe import Probe, StepFailure, get_json, non_empty_list
from checks.result import not_configured, timed

LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"
KNOWN_ARTIST = "Radiohead"
KNOWN_TRACK = "Creep"


def run():
    api_key = os.environ.get("LASTFM_API_KEY", "").strip()
    if not api_key:
        return not_configured("LASTFM_API_KEY is not set")

    def lastfm(method, **params):
        body = get_json(LASTFM_API_URL, params={
            "method": method, "api_key": api_key, "format": "json", "autocorrect": 1, **params})
        # Last.fm reports errors (bad key, suspended key, unknown method) as 200 with an "error" field.
        if "error" in body:
            raise StepFailure(f"Last.fm error {body['error']}: {body.get('message', 'no message')}")
        return body

    def track_tags():
        # SpotifyDashTool's genre lookup tries the track's tags first...
        body = lastfm("track.getInfo", artist=KNOWN_ARTIST, track=KNOWN_TRACK)
        tags = non_empty_list(body, "track.toptags.tag")
        return f"{len(tags)} track tags, top '{tags[0].get('name')}'"

    def artist_tags():
        # ...and falls back to the artist's tags when the track has none.
        body = lastfm("artist.getTopTags", artist=KNOWN_ARTIST)
        tags = non_empty_list(body, "toptags.tag")
        return f"{len(tags)} artist tags, top '{tags[0].get('name')}'"

    def do_check():
        probe = Probe(api_key)
        probe.step("SpotifyDashTool genres (track.getInfo tags)", track_tags)
        probe.step("SpotifyDashTool genre fallback (artist.getTopTags)", artist_tags)
        return probe.result()

    return timed(do_check)
