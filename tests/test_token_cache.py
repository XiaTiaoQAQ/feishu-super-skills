"""Tests for the persistent token cache."""
from __future__ import annotations

import json
import threading

import pytest

from feishu_super import token_cache


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(token_cache, "CACHE_DIR", tmp_path)
    return tmp_path


def test_save_then_load_roundtrip(cache_dir):
    token_cache.save("cli_app", "tk_value", expires_in=7200)
    loaded = token_cache.load("cli_app")
    assert loaded is not None
    assert loaded.token == "tk_value"
    assert loaded.is_fresh()


def test_load_missing_returns_none(cache_dir):
    assert token_cache.load("cli_nope") is None


def test_purge_removes_file(cache_dir):
    token_cache.save("cli_app", "tk_value", expires_in=7200)
    assert token_cache.load("cli_app") is not None
    token_cache.purge("cli_app")
    assert token_cache.load("cli_app") is None


def test_save_is_atomic_under_concurrent_writers(cache_dir):
    """Two threads writing different tokens for the same app_id must always
    leave the cache in a state where load() returns one of the two values
    intact — never a half-written JSON.

    This is the regression test for the os.replace-based atomic write.
    """
    barrier = threading.Barrier(20)
    errors = []

    def writer(i: int):
        barrier.wait()
        try:
            token_cache.save("cli_app", f"tk_{i:02d}", expires_in=7200)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors

    # The final file must be a valid one of the 20 written values.
    loaded = token_cache.load("cli_app")
    assert loaded is not None
    assert loaded.token.startswith("tk_")
    assert int(loaded.token.split("_")[1]) in range(20)

    # And no leftover *.tmp.<pid> files lying around.
    leftovers = list(cache_dir.glob("*.tmp.*"))
    assert leftovers == []


def test_save_handles_corrupt_existing_file(cache_dir):
    """If the existing cache file is gibberish (e.g. left over from a crash),
    save() should still succeed and overwrite cleanly."""
    p = token_cache._cache_path("cli_app")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json at all", encoding="utf-8")
    token_cache.save("cli_app", "tk_after", expires_in=7200)

    loaded = token_cache.load("cli_app")
    assert loaded is not None
    assert loaded.token == "tk_after"

    # Confirm the file is also a valid JSON dict (not appended garbage).
    parsed = json.loads(p.read_text(encoding="utf-8"))
    assert parsed["token"] == "tk_after"
