import time


class CheckResult:
    def __init__(self, status, message, latency_ms=None):
        self.status = status
        self.message = message
        self.latency_ms = latency_ms

    def to_dict(self):
        return {"status": self.status, "message": self.message, "latency_ms": self.latency_ms}


def ok(message, latency_ms=None):
    return CheckResult("ok", message, latency_ms)


def fail(message, latency_ms=None):
    return CheckResult("fail", message, latency_ms)


def not_configured(message):
    return CheckResult("not_configured", message)


def timed(fn):
    start = time.monotonic()
    result = fn()
    elapsed_ms = round((time.monotonic() - start) * 1000)
    result.latency_ms = elapsed_ms
    return result
