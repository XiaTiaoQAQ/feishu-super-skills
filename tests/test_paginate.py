"""Tests for the shared paginate_all helper."""

from __future__ import annotations

import pytest

from feishu_super.commands._common import MAX_PAGES, paginate_all


def _fake_pages(pages: list[dict]):
    """Return a fetch function that serves pre-canned pages in order."""
    index = {"i": 0}

    def fetch(_pt):
        i = index["i"]
        index["i"] += 1
        if i >= len(pages):
            return {"items": [], "has_more": False}
        return pages[i]

    return fetch


def test_paginate_single_page():
    fetch = _fake_pages([{"items": [1, 2, 3], "has_more": False}])
    assert paginate_all(fetch, fetch_all=True) == [1, 2, 3]


def test_paginate_multi_page():
    fetch = _fake_pages(
        [
            {"items": [1, 2], "has_more": True, "page_token": "t1"},
            {"items": [3, 4], "has_more": True, "page_token": "t2"},
            {"items": [5], "has_more": False},
        ]
    )
    assert paginate_all(fetch, fetch_all=True) == [1, 2, 3, 4, 5]


def test_paginate_no_fetch_all_stops_after_first_page():
    fetch = _fake_pages(
        [
            {"items": [1, 2], "has_more": True, "page_token": "t1"},
            {"items": [3, 4], "has_more": False},
        ]
    )
    assert paginate_all(fetch, fetch_all=False) == [1, 2]


def test_paginate_handles_none_response():
    def fetch(_pt):
        return None

    assert paginate_all(fetch, fetch_all=True) == []


def test_paginate_truncation_warns(capsys):
    # Always says has_more=True → should hit MAX_PAGES cap and warn.
    def fetch(_pt):
        return {"items": ["x"], "has_more": True, "page_token": "next"}

    items = paginate_all(fetch, fetch_all=True)
    assert len(items) == MAX_PAGES
    err = capsys.readouterr().err
    assert "达到" in err and "上限" in err


def test_paginate_stops_when_no_page_token_despite_has_more():
    # Defensive: has_more=True but page_token missing — must not infinite loop.
    fetch = _fake_pages(
        [
            {"items": [1], "has_more": True, "page_token": None},
        ]
    )
    assert paginate_all(fetch, fetch_all=True) == [1]
