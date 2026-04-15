"""Tests for LarkClient concurrency primitives.

Covers two orthogonal guarantees:
  1. _get_token holds an RLock so 10 threads racing through the cold cache
     hit _fetch_token at most once instead of N times.
  2. The fast path (token already present) does NOT take the lock — important
     for the >99% case where the cache is warm.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from feishu_super import token_cache
from feishu_super.client import LarkClient


@pytest.fixture
def fresh_token_dir(tmp_path, monkeypatch):
    """Point the persistent token cache at a tmp dir to keep tests hermetic."""
    monkeypatch.setattr(token_cache, "CACHE_DIR", tmp_path)
    yield tmp_path


def test_get_token_serializes_concurrent_first_callers(fresh_token_dir):
    """10 threads racing on a cold cache must trigger _fetch_token exactly once.

    Without the RLock around the cold-cache code path, every thread that
    finds `_token is None` would race past the check and each issue an
    independent auth request.
    """
    client = LarkClient(app_id="cli_test", app_secret="secret")
    fetch_calls = {"count": 0}
    fetch_lock = threading.Lock()

    def fake_fetch():
        # Simulate network latency so threads pile up in the lock.
        with fetch_lock:
            fetch_calls["count"] += 1
        time.sleep(0.05)
        client._token = "tk_fake"
        return "tk_fake"

    with patch.object(client, "_fetch_token", side_effect=fake_fetch):
        with ThreadPoolExecutor(max_workers=10) as ex:
            results = list(ex.map(lambda _i: client._get_token(), range(10)))

    assert all(r == "tk_fake" for r in results)
    assert fetch_calls["count"] == 1, (
        f"_fetch_token should be called exactly once under contention, "
        f"got {fetch_calls['count']}"
    )


def test_get_token_warm_cache_is_lock_free(fresh_token_dir):
    """When _token is already set, _get_token must not even touch the lock.

    We verify this by replacing the lock with one that records acquire calls.
    """
    client = LarkClient(app_id="cli_test", app_secret="secret")
    client._token = "tk_warm"  # warm the cache directly

    acquire_count = {"n": 0}
    real_lock = client._token_lock

    class CountingLock:
        def __enter__(self):
            acquire_count["n"] += 1
            return real_lock.__enter__()

        def __exit__(self, *a):
            return real_lock.__exit__(*a)

    client._token_lock = CountingLock()  # type: ignore[assignment]
    token = client._get_token()
    assert token == "tk_warm"
    assert acquire_count["n"] == 0


def test_invalidate_token_is_locked(fresh_token_dir):
    """_invalidate_token must take the lock, otherwise a concurrent
    _get_token could observe a half-cleared state."""
    client = LarkClient(app_id="cli_test", app_secret="secret")
    client._token = "tk_old"

    acquire_count = {"n": 0}
    real_lock = client._token_lock

    class CountingLock:
        def __enter__(self):
            acquire_count["n"] += 1
            return real_lock.__enter__()

        def __exit__(self, *a):
            return real_lock.__exit__(*a)

    client._token_lock = CountingLock()  # type: ignore[assignment]
    client._invalidate_token()
    assert client._token is None
    assert acquire_count["n"] == 1
