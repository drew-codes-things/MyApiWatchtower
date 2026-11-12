import stat
import time

import app as watchtower
import results_store

RUN_WAIT_SECONDS = 5
DAMAGED_JSON = '{"checked_at": "2026-11-12T21:00:00+00:00", "results": {"demo": {"sta'


def damage_results_file(tmp_path):
    (tmp_path / "results.json").write_text(DAMAGED_JSON)


def wait_for_run_to_finish():
    deadline = time.monotonic() + RUN_WAIT_SECONDS
    while watchtower.get_run_state()["active"] and time.monotonic() < deadline:
        time.sleep(0.05)
    return not watchtower.get_run_state()["active"]


def test_a_damaged_results_file_reads_as_empty(client, tmp_path):
    damage_results_file(tmp_path)
    assert results_store.load_results() == {"checked_at": None, "trigger": None, "results": {}}
    assert client.get("/").status_code == 200


def test_a_damaged_results_file_does_not_wedge_the_run_state(client, tmp_path, monkeypatch):
    monkeypatch.setattr(watchtower, "_run_state", {"active": False, "completed": 0, "total": 0})
    damage_results_file(tmp_path)
    assert watchtower.start_run("manual") is True
    assert wait_for_run_to_finish()
    assert watchtower.start_run("manual") is True
    assert wait_for_run_to_finish()
    assert set(results_store.load_results()["results"]) == {"demo", "plain"}


def test_results_are_written_privately_and_atomically(client, tmp_path):
    results_store.save_results("2026-11-12T21:00:00+00:00", {"demo": {"status": "ok"}}, "manual")
    assert stat.S_IMODE((tmp_path / "results.json").stat().st_mode) == 0o600
    assert not (tmp_path / "results.json.tmp").exists()
