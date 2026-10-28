import os

from plexapi.exceptions import NotFound
from plexapi.myplex import MyPlexAccount

from checks.probe import Probe, StepFailure
from checks.result import not_configured, timed

DEFAULT_LIBRARIES = "Movies,Anime Shows,Anime Shows - High"


def _expected_libraries():
    raw = os.environ.get("PLEX_LIBRARIES", DEFAULT_LIBRARIES)
    return [name.strip() for name in raw.split(",") if name.strip()]


def run():
    token = os.environ.get("PLEX_TOKEN", "").strip()
    server_name = os.environ.get("PLEX_SERVER_NAME", "").strip()
    if not token or not server_name:
        return not_configured("PLEX_TOKEN/PLEX_SERVER_NAME are not set")

    def do_check():
        probe = Probe(token)
        state = {}

        def account():
            state["account"] = MyPlexAccount(token=token)
            return f"plex.tv accepted the token ({state['account'].username})"

        def connect():
            try:
                resource = state["account"].resource(server_name)
            except NotFound as error:
                names = ", ".join(r.name for r in state["account"].resources() if "server" in (r.provides or ""))
                raise StepFailure(f"no server called '{server_name}' on this account (servers: {names or 'none'})") from error
            state["server"] = resource.connect()
            return f"connected to '{state['server'].friendlyName}' (v{state['server'].version})"

        def library(name):
            try:
                section = state["server"].library.section(name)
            except NotFound as error:
                raise StepFailure(f"no library called '{name}' - it was renamed or removed") from error
            state.setdefault("sample_section", section)
            return f"{section.type} library with {section.totalSize} items"

        def item_metadata():
            sample = state["sample_section"].search(maxresults=1)
            if not sample:
                raise StepFailure("the library returned no items to look up")
            item = state["server"].fetchItem(int(sample[0].ratingKey))
            missing = [name for name in ("title", "type", "year", "thumbUrl", "duration")
                       if getattr(item, name, None) in (None, "")]
            if missing:
                raise StepFailure(f"'{item.title}' is missing {', '.join(missing)}, so the presence would be incomplete")
            return f"fetched '{item.title}' ({item.year}) by ratingKey"

        signed_in = probe.step("plex.tv sign-in (MyPlexAccount)", account)
        connected = probe.step(f"Connect to '{server_name}'", connect, needs=[("a plex.tv sign-in", signed_in)])
        for name in _expected_libraries():
            probe.step(f"Library '{name}'", lambda name=name: library(name), needs=[("a server connection", connected)])
        probe.step("PlexDiscordPresence item lookup (fetchItem)", item_metadata,
                   needs=[("a library", state.get("sample_section"))])
        return probe.result()

    return timed(do_check)
