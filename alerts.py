import os

import requests

ALERT_TIMEOUT_SECONDS = 10
MAX_ALERT_LINES = 20
HEALTHY = {"ok", "warn"}


def status_changes(previous, current, labels):
    changes = []
    for check_id, result in current.items():
        before = (previous.get(check_id) or {}).get("status")
        after = result["status"]
        if before in HEALTHY and after == "fail":
            changes.append(f"🔴 {labels.get(check_id, check_id)} is failing: {result['message']}")
        elif before == "fail" and after in HEALTHY:
            changes.append(f"🟢 {labels.get(check_id, check_id)} is working again")
    return changes


def send_alert(changes):
    webhook_url = os.environ.get("DISCORD_ALERT_WEBHOOK_URL", "").strip()
    if not webhook_url or not changes:
        return False
    lines = changes[:MAX_ALERT_LINES]
    if len(changes) > MAX_ALERT_LINES:
        lines.append(f"...and {len(changes) - MAX_ALERT_LINES} more")
    content = "**MyApiWatchtower**\n" + "\n".join(lines)
    try:
        response = requests.post(webhook_url, json={"content": content[:2000]}, timeout=ALERT_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as error:
        print(f"Discord alert failed: {type(error).__name__}", flush=True)
        return False
    return True
