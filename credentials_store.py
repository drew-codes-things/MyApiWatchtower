import json
import os
import threading
from urllib.parse import urlparse

CREDENTIALS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
MAX_VALUE_LENGTH = 512
_lock = threading.Lock()


class InvalidCredential(ValueError):
    pass


def _read():
    if not os.path.exists(CREDENTIALS_PATH):
        return {}
    with open(CREDENTIALS_PATH, "r", encoding="utf-8") as file:
        saved = json.load(file)
    if not isinstance(saved, dict):
        raise ValueError(f"{CREDENTIALS_PATH} should hold a JSON object")
    return {str(key): str(value) for key, value in saved.items()}


def _write(saved):
    temporary_path = CREDENTIALS_PATH + ".tmp"
    descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(saved, file, indent=2, sort_keys=True)
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, CREDENTIALS_PATH)


def apply_saved_credentials():
    with _lock:
        saved = _read()
    os.environ.update(saved)
    return sorted(saved)


def clean_value(name, value, is_url=False):
    value = value.strip()
    if len(value) > MAX_VALUE_LENGTH:
        raise InvalidCredential(f"{name} is longer than {MAX_VALUE_LENGTH} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise InvalidCredential(f"{name} contains control characters")
    if value and is_url:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or any(c.isspace() for c in value):
            raise InvalidCredential(f"{name} must be a full http:// or https:// link")
    return value


def save_credentials(updates):
    if not updates:
        return []
    with _lock:
        saved = _read()
        saved.update(updates)
        _write(saved)
    os.environ.update(updates)
    return sorted(updates)


def is_set(name):
    return bool(os.environ.get(name, "").strip())
