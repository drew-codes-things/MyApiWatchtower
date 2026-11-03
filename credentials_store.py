import os
import threading
from urllib.parse import urlparse

from private_json import read_private_json, write_private_json

CREDENTIALS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
MAX_VALUE_LENGTH = 512
_lock = threading.Lock()


class InvalidCredential(ValueError):
    pass


def _read():
    saved = read_private_json(CREDENTIALS_PATH, {})
    if not isinstance(saved, dict):
        raise ValueError(f"{CREDENTIALS_PATH} should hold a JSON object")
    return {str(key): str(value) for key, value in saved.items()}


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
        write_private_json(CREDENTIALS_PATH, saved)
    os.environ.update(updates)
    return sorted(updates)


def is_set(name):
    return bool(os.environ.get(name, "").strip())
