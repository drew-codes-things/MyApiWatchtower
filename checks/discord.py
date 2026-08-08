import os

import requests

from checks.result import fail, not_configured, ok, timed

DISCORD_ME_URL = "https://discord.com/api/v10/users/@me"


def run(env_var):
    token = os.environ.get(env_var, "").strip()
    if not token:
        return not_configured(f"{env_var} is not set")

    def do_check():
        headers = {"Authorization": f"Bot {token}"}
        try:
            response = requests.get(DISCORD_ME_URL, headers=headers, timeout=10)
        except requests.RequestException as error:
            return fail(f"Request failed: {error}")
        if response.status_code == 200:
            body = response.json()
            return ok(f"Discord accepted the token (bot: {body.get('username', 'unknown')})")
        if response.status_code == 401:
            return fail("Discord rejected the token (401 Unauthorized)")
        return fail(f"Discord returned unexpected status {response.status_code}")

    return timed(do_check)
