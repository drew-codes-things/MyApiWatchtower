from registry import CHECKS, get_check

REQUIRED_KEYS = {"id", "label", "projects", "kind", "run"}


def test_every_check_has_required_keys():
    for check in CHECKS:
        assert REQUIRED_KEYS.issubset(check.keys())


def test_check_ids_are_unique():
    ids = [check["id"] for check in CHECKS]
    assert len(ids) == len(set(ids))


def test_kind_is_api_or_reachability():
    for check in CHECKS:
        assert check["kind"] in {"api", "reachability"}


def test_projects_is_a_non_empty_list_of_strings():
    for check in CHECKS:
        assert isinstance(check["projects"], list)
        assert len(check["projects"]) > 0
        assert all(isinstance(project, str) for project in check["projects"])


def test_run_is_callable():
    for check in CHECKS:
        assert callable(check["run"])


def test_get_check_returns_matching_entry():
    first = CHECKS[0]
    assert get_check(first["id"]) is first


def test_get_check_returns_none_for_unknown_id():
    assert get_check("not-a-real-id") is None
