import results_store


def test_load_results_with_no_file_returns_empty_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(results_store, "RESULTS_PATH", str(tmp_path / "results.json"))
    loaded = results_store.load_results()
    assert loaded == {"checked_at": None, "results": {}}


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(results_store, "RESULTS_PATH", str(tmp_path / "results.json"))
    results = {"tmdb": {"status": "ok", "message": "fine", "latency_ms": 42}}
    saved = results_store.save_results("2026-08-08T00:00:00+00:00", results)
    loaded = results_store.load_results()
    assert loaded == saved
    assert loaded["results"]["tmdb"]["status"] == "ok"
