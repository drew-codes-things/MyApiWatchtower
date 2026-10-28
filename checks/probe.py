"""Step-by-step checking, so a result says exactly which request or field broke.

A check runs as a series of named steps that each mirror one thing a dependent
project really does (an endpoint it calls, a field it reads). A step passes,
warns (works, but something a project relies on is degraded), fails, or is
skipped because an earlier step it needed failed.
"""
import re
import time

import requests

from checks.result import fail, ok, redact, warn

DEFAULT_TIMEOUT = 15

# Statuses with a known, specific meaning, so a failure reads as a cause, not just a number.
STATUS_MEANINGS = {
    400: "bad request - the API no longer accepts these parameters",
    401: "unauthorized - the key or token was rejected",
    403: "forbidden - access to this endpoint was refused (blocked, or not allowed for this key/app)",
    404: "not found - the endpoint or item no longer exists",
    410: "gone - the endpoint has been removed",
    429: "rate limited",
    500: "internal server error on their side",
    502: "bad gateway on their side",
    503: "service unavailable (down or overloaded)",
    504: "gateway timeout on their side",
    520: "Cloudflare 520 - their origin returned an unknown error",
    521: "Cloudflare 521 - their origin server is down",
    522: "Cloudflare 522 - Cloudflare timed out connecting to their origin",
    523: "Cloudflare 523 - their origin is unreachable",
    524: "Cloudflare 524 - their origin took too long to respond",
    525: "Cloudflare 525 - TLS handshake with their origin failed",
    526: "Cloudflare 526 - their origin's certificate is invalid",
}

FIELD_PATH_PART = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


class StepFailure(Exception):
    """The step found something a dependent project cannot work with."""


class StepWarning(Exception):
    """The step works, but in a degraded way a dependent project has to fall back from."""


def describe_status(response):
    meaning = STATUS_MEANINGS.get(response.status_code, "unexpected status")
    text = f"HTTP {response.status_code} ({meaning})"
    retry_after = response.headers.get("Retry-After")
    if response.status_code == 429 and retry_after:
        text += f", retry after {retry_after}s"
    return text


def describe_request_error(error):
    if isinstance(error, requests.exceptions.SSLError):
        return f"TLS/certificate error: {error}"
    if isinstance(error, requests.exceptions.ConnectTimeout):
        return "timed out connecting"
    if isinstance(error, requests.exceptions.ReadTimeout):
        return "connected, but timed out waiting for a response"
    if isinstance(error, requests.exceptions.ConnectionError):
        text = str(error)
        if "Name or service not known" in text or "NameResolutionError" in text or "getaddrinfo" in text:
            return "DNS lookup failed - the hostname doesn't resolve"
        if "Connection refused" in text:
            return "connection refused - nothing is listening"
        if "Network is unreachable" in text or "timed out" in text:
            return "could not reach the server at all (it's down, or blocking this network)"
        return f"could not connect: {text}"
    return f"request failed: {error}"


def http(method, url, *, expect=(200,), **kwargs):
    """Make one request and fail the step with a precise reason unless the status is expected."""
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    try:
        response = requests.request(method, url, **kwargs)
    except requests.RequestException as error:
        raise StepFailure(describe_request_error(error)) from error
    if response.status_code not in expect:
        raise StepFailure(describe_status(response))
    return response


def get_json(url, **kwargs):
    response = http("GET", url, **kwargs)
    try:
        return response.json()
    except ValueError as error:
        content_type = response.headers.get("Content-Type", "unknown")
        preview = response.text[:80].replace("\n", " ")
        raise StepFailure(f"expected JSON but got {content_type}: {preview!r}") from error


def field(data, path):
    """Read a dotted/indexed path like 'results[0].poster_path', failing the step if any part is missing."""
    current = data
    walked = ""
    for key, index in FIELD_PATH_PART.findall(path):
        if index:
            position = int(index)
            if not isinstance(current, list) or position >= len(current):
                raise StepFailure(f"'{walked or 'response'}' has no item [{position}] (response shape changed or empty)")
            current = current[position]
            walked += f"[{position}]"
        else:
            if not isinstance(current, dict) or key not in current:
                raise StepFailure(f"field '{walked + '.' if walked else ''}{key}' is missing (response shape changed)")
            current = current[key]
            walked += f".{key}" if walked else key
    return current


def require_fields(data, *paths):
    """Fail the step if any of the fields a project reads is missing."""
    for path in paths:
        field(data, path)


def non_empty_list(data, path):
    value = field(data, path)
    if not isinstance(value, list) or not value:
        raise StepFailure(f"'{path}' is empty, so there is nothing for the project to use")
    return value


class Probe:
    def __init__(self, *secrets):
        self.steps = []
        self.secrets = [secret for secret in secrets if secret]

    def step(self, name, fn, needs=()):
        """Run one step. fn returns a detail string or (detail, value); the value is returned for later steps."""
        missing = [label for label, value in needs if value is None]
        if missing:
            self._record(name, "skipped", f"needs {', '.join(missing)} from an earlier step", None)
            return None
        start = time.monotonic()
        try:
            outcome = fn()
        except StepWarning as reason:
            self._record(name, "warn", str(reason), start)
            return None
        except StepFailure as reason:
            self._record(name, "fail", str(reason), start)
            return None
        except Exception as error:
            self._record(name, "fail", f"check crashed: {type(error).__name__}: {error}", start)
            return None
        detail, value = outcome if isinstance(outcome, tuple) else (outcome, True)
        self._record(name, "ok", detail, start)
        return value

    def _record(self, name, status, detail, start):
        latency_ms = round((time.monotonic() - start) * 1000) if start is not None else None
        self.steps.append({
            "name": name,
            "status": status,
            "detail": redact(str(detail), *self.secrets),
            "latency_ms": latency_ms,
        })

    def result(self):
        failed = [s for s in self.steps if s["status"] == "fail"]
        warned = [s for s in self.steps if s["status"] == "warn"]
        skipped = [s for s in self.steps if s["status"] == "skipped"]
        passed = len(self.steps) - len(failed) - len(warned) - len(skipped)
        tally = f"{passed}/{len(self.steps)} steps passed"
        if failed:
            first = failed[0]
            return fail(f"{tally}. First failure, '{first['name']}': {first['detail']}", steps=self.steps)
        if warned:
            first = warned[0]
            return warn(f"{tally}. '{first['name']}': {first['detail']}", steps=self.steps)
        return ok(f"All {len(self.steps)} steps passed", steps=self.steps)
