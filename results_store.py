import json
import os
import threading

RESULTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.json")
_lock = threading.Lock()


def load_results():
    if not os.path.exists(RESULTS_PATH):
        return {"checked_at": None, "trigger": None, "results": {}}
    with open(RESULTS_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def save_results(checked_at, results, trigger):
    payload = {"checked_at": checked_at, "trigger": trigger, "results": results}
    with _lock:
        with open(RESULTS_PATH, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2)
    return payload
