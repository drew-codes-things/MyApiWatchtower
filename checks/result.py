import time


class CheckResult:
    def __init__(self, status, message, latency_ms=None, steps=None):
        self.status = status
        self.message = message
        self.latency_ms = latency_ms
        self.steps = steps or []

    def to_dict(self):
        return {
            "status": self.status,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "steps": self.steps,
        }


def ok(message, latency_ms=None, steps=None):
    return CheckResult("ok", message, latency_ms, steps)


def warn(message, latency_ms=None, steps=None):
    return CheckResult("warn", message, latency_ms, steps)


def fail(message, latency_ms=None, steps=None):
    return CheckResult("fail", message, latency_ms, steps)


def not_configured(message):
    return CheckResult("not_configured", message)


def timed(fn):
    start = time.monotonic()
    result = fn()
    elapsed_ms = round((time.monotonic() - start) * 1000)
    result.latency_ms = elapsed_ms
    return result


def redact(text, *secrets):
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted
