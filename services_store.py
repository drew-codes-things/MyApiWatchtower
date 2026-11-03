import os
import re
import secrets
import threading

from credentials_store import InvalidCredential, clean_value
from private_json import read_private_json, write_private_json

SERVICES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "services.json")
METHODS = ("GET", "HEAD", "POST")
DEFAULT_EXPECTED_STATUS = "200-399"
DEFAULT_TIMEOUT_SECONDS = 15
MAX_TIMEOUT_SECONDS = 60
MAX_NAME_LENGTH = 60
MAX_CONTAINS_LENGTH = 200
MAX_TAGS = 8
HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
STATUS_PART = re.compile(r"^(\d{3})(?:-(\d{3}))?$")
_lock = threading.Lock()


class InvalidService(ValueError):
    pass


def load_services():
    with _lock:
        saved = read_private_json(SERVICES_PATH, [])
    if not isinstance(saved, list):
        raise ValueError(f"{SERVICES_PATH} should hold a JSON list")
    return saved


def get_service(service_id):
    return next((service for service in load_services() if service["id"] == service_id), None)


def save_service(service):
    with _lock:
        saved = read_private_json(SERVICES_PATH, [])
        saved = [existing for existing in saved if existing["id"] != service["id"]] + [service]
        write_private_json(SERVICES_PATH, saved)
    return service


def delete_service(service_id):
    with _lock:
        saved = read_private_json(SERVICES_PATH, [])
        kept = [service for service in saved if service["id"] != service_id]
        if len(kept) == len(saved):
            return False
        write_private_json(SERVICES_PATH, kept)
    return True


def new_service_id(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "service"
    return f"svc-{slug}-{secrets.token_hex(3)}"


def expected_status_codes(text):
    codes = set()
    for part in (text or DEFAULT_EXPECTED_STATUS).replace(" ", "").split(","):
        match = STATUS_PART.match(part)
        if not match:
            raise InvalidService(f"'{part}' isn't a status code or range like 200 or 200-299")
        low = int(match.group(1))
        high = int(match.group(2) or low)
        if not 100 <= low <= high <= 599:
            raise InvalidService(f"'{part}' is outside the HTTP status range 100-599")
        codes.update(range(low, high + 1))
    return codes


def _text(form, key, label, max_length):
    value = clean_value(label, form.get(key, ""))
    if len(value) > max_length:
        raise InvalidService(f"{label} is longer than {max_length} characters")
    return value


def _timeout(form):
    raw = form.get("timeout", "").strip() or str(DEFAULT_TIMEOUT_SECONDS)
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_TIMEOUT_SECONDS:
        raise InvalidService(f"Timeout must be a whole number of seconds from 1 to {MAX_TIMEOUT_SECONDS}")
    return int(raw)


def _tags(form):
    raw = _text(form, "tags", "Tags", 300)
    tags = [tag.strip() for tag in raw.split(",") if tag.strip()]
    if len(tags) > MAX_TAGS:
        raise InvalidService(f"Use at most {MAX_TAGS} tags")
    return tags


def service_from_form(form, existing=None):
    try:
        name = _text(form, "name", "Name", MAX_NAME_LENGTH)
        url = clean_value("URL", form.get("url", ""), is_url=True)
        expected_status = _text(form, "expected_status", "Expected status", 60) or DEFAULT_EXPECTED_STATUS
        contains = _text(form, "contains", "Response must contain", MAX_CONTAINS_LENGTH)
        header_name = _text(form, "header_name", "Header name", 64)
        header_value = clean_value("Header value", form.get("header_value", ""))
    except InvalidCredential as error:
        raise InvalidService(str(error)) from error
    method = form.get("method", "GET").strip().upper()
    if not name:
        raise InvalidService("Give the service a name")
    if not url:
        raise InvalidService("Give the service a URL to check")
    if method not in METHODS:
        raise InvalidService(f"Method must be one of {', '.join(METHODS)}")
    expected_status_codes(expected_status)
    if header_name and not HEADER_NAME.match(header_name):
        raise InvalidService("Header name can only use letters, numbers and dashes")
    if existing and header_name == existing.get("header_name") and not header_value:
        header_value = existing.get("header_value", "")
    if header_value and not header_name:
        raise InvalidService("Add a header name for the header value")
    if contains and method == "HEAD":
        raise InvalidService("A HEAD request has no body, so 'Response must contain' can't be checked")
    return {
        "id": existing["id"] if existing else new_service_id(name),
        "name": name,
        "url": url,
        "method": method,
        "expected_status": expected_status,
        "contains": contains,
        "header_name": header_name,
        "header_value": header_value if header_name else "",
        "timeout": _timeout(form),
        "tags": _tags(form),
    }
