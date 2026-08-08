import os

from plexapi.myplex import MyPlexAccount

from checks.result import fail, not_configured, ok, redact, timed


def run():
    token = os.environ.get("PLEX_TOKEN", "").strip()
    server_name = os.environ.get("PLEX_SERVER_NAME", "").strip()
    if not token or not server_name:
        return not_configured("PLEX_TOKEN/PLEX_SERVER_NAME are not set")

    def do_check():
        try:
            account = MyPlexAccount(token=token)
            server = account.resource(server_name).connect()
        except Exception as error:
            return fail(redact(f"Plex connection failed: {error}", token))
        return ok(f"Connected to Plex server '{server.friendlyName}'")

    return timed(do_check)
