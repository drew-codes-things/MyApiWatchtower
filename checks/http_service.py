from checks.probe import Probe, StepFailure, http
from checks.result import timed
from services_store import expected_status_codes

PREVIEW_LENGTH = 80


def run_service(service):
    header_value = service.get("header_value", "")
    headers = {service["header_name"]: header_value} if service.get("header_name") and header_value else {}

    def do_check():
        probe = Probe(header_value)

        def request():
            response = http(service["method"], service["url"], expect=tuple(expected_status_codes(service.get("expected_status"))),
                            headers=headers, timeout=service.get("timeout"), allow_redirects=True)
            return f"HTTP {response.status_code}", response

        response = probe.step(f"{service['method']} {service['url']}", request)
        if service.get("contains"):
            def body_contains():
                if service["contains"] not in response.text:
                    preview = response.text[:PREVIEW_LENGTH].replace("\n", " ")
                    raise StepFailure(f"the response doesn't contain {service['contains']!r} (it starts {preview!r})")
                return f"found {service['contains']!r}"

            probe.step("Response contains the expected text", body_contains, needs=[("a response", response)])
        return probe.result()

    return timed(do_check)
