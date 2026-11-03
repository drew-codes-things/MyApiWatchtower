import os

from checks.probe import Probe, StepFailure, StepWarning, get_json, require_fields
from checks.result import not_configured, timed
from checks.settings import configured_url

DISCORD_API = "https://discord.com/api/v10"


def discord_api():
    return configured_url("DISCORD_API_URL", DISCORD_API)
LOW_SESSION_STARTS = 50
MESSAGE_CONTENT_INTENT = 1 << 18
MESSAGE_CONTENT_INTENT_LIMITED = 1 << 19


def _bot_headers(token):
    return {"Authorization": f"Bot {token}"}


def _bot_steps(probe, token):
    headers = _bot_headers(token)

    def identity():
        body = get_json(f"{discord_api()}/users/@me", headers=headers)
        require_fields(body, "id", "username")
        if not body.get("bot"):
            raise StepFailure("this token belongs to a user account, not a bot")
        return f"token accepted, bot '{body['username']}'"

    def gateway():
        body = get_json(f"{discord_api()}/gateway/bot", headers=headers)
        require_fields(body, "url", "session_start_limit.remaining", "session_start_limit.reset_after")
        remaining = body["session_start_limit"]["remaining"]
        if remaining < LOW_SESSION_STARTS:
            hours = round(body["session_start_limit"]["reset_after"] / 3_600_000, 1)
            raise StepWarning(f"only {remaining} gateway logins left before a reset in {hours}h - "
                              "something is reconnecting in a loop")
        return f"{remaining} gateway logins left today"

    valid = probe.step("Bot token (/users/@me)", identity)
    probe.step("Gateway logins left (/gateway/bot)", gateway, needs=[("a valid token", valid)])
    return valid


def run(env_var):
    token = os.environ.get(env_var, "").strip()
    if not token:
        return not_configured(f"{env_var} is not set")

    def do_check():
        probe = Probe(token)
        _bot_steps(probe, token)
        return probe.result()

    return timed(do_check)


def run_reddit_relay():
    webhook_url = os.environ.get("REDDITV4_WEBHOOK_URL", "").strip()
    token = os.environ.get("REDDITV4_BOT_TOKEN", "").strip()
    channel_id = os.environ.get("REDDITV4_CHANNEL_ID", "").strip()
    if not webhook_url or not token or not channel_id:
        return not_configured("REDDITV4_WEBHOOK_URL/REDDITV4_BOT_TOKEN/REDDITV4_CHANNEL_ID are not set")

    def do_check():
        probe = Probe(token, webhook_url)
        headers = _bot_headers(token)

        def webhook():
            body = get_json(webhook_url)
            require_fields(body, "channel_id")
            if body["channel_id"] != channel_id:
                raise StepFailure(f"the webhook posts into channel {body['channel_id']}, but the poller "
                                  f"reads {channel_id}, so nothing would reach the relay")
            return "webhook alive, posting into the poller's channel"

        def message_content_intent():
            body = get_json(f"{discord_api()}/applications/@me", headers=headers)
            flags = body.get("flags", 0)
            if not flags & (MESSAGE_CONTENT_INTENT | MESSAGE_CONTENT_INTENT_LIMITED):
                raise StepFailure("Message Content Intent is off, so the poller sees every RELAY: message "
                                  "as empty (Developer Portal > Bot > Privileged Gateway Intents)")
            return "Message Content Intent is on"

        def read_channel():
            messages = get_json(f"{discord_api()}/channels/{channel_id}/messages", params={"limit": 20},
                                headers=headers)
            relay_messages = [m for m in messages if (m.get("content") or "").startswith("RELAY:")]
            if messages and not any(m.get("content") for m in messages):
                raise StepFailure("messages come back with empty content - the intent isn't taking effect")
            return f"read {len(messages)} recent messages, {len(relay_messages)} of them RELAY: batches"

        probe.step("Devvit app's webhook (/webhooks/{id}/{token})", webhook)
        valid = _bot_steps(probe, token)
        needs_bot = [("a valid bot token", valid)]
        probe.step("Message Content Intent (/applications/@me)", message_content_intent, needs=needs_bot)
        probe.step("discord-poller read (/channels/{id}/messages)", read_channel, needs=needs_bot)
        return probe.result()

    return timed(do_check)
