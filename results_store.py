import os
import threading

from private_json import read_private_json, write_private_json

RESULTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.json")
_lock = threading.Lock()


def empty_results():
    return {"checked_at": None, "trigger": None, "results": {}}


def load_results():
    try:
        stored = read_private_json(RESULTS_PATH, None)
    except ValueError as error:
        print(f"Ignoring damaged {RESULTS_PATH}: {error}", flush=True)
        return empty_results()
    if stored is None:
        return empty_results()
    if not isinstance(stored, dict) or not isinstance(stored.get("results"), dict):
        print(f"Ignoring {RESULTS_PATH}: it does not hold a results object", flush=True)
        return empty_results()
    return stored


def save_results(checked_at, results, trigger):
    payload = {"checked_at": checked_at, "trigger": trigger, "results": results}
    with _lock:
        write_private_json(RESULTS_PATH, payload)
    return payload


def save_single_result(check_id, result, checked_at):
    with _lock:
        stored = load_results()
        stored["results"][check_id] = result
        stored["results"][check_id]["checked_at"] = checked_at
        write_private_json(RESULTS_PATH, stored)
    return stored


def remove_result(check_id):
    with _lock:
        stored = load_results()
        if stored["results"].pop(check_id, None) is None:
            return False
        write_private_json(RESULTS_PATH, stored)
    return True
