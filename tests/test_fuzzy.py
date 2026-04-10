"""Tests for the client-side fuzzy matcher used as fallback in records search."""

from __future__ import annotations

from feishu_super.commands.records import _client_fuzzy_filter


def _rec(rid: str, fields: dict) -> dict:
    return {"record_id": rid, "fields": fields}


def test_client_fuzzy_matches_plain_string():
    items = [
        _rec("r1", {"name": "张三", "age": 18}),
        _rec("r2", {"name": "李四", "age": 20}),
    ]
    assert _client_fuzzy_filter(items, "张") == [items[0]]


def test_client_fuzzy_case_insensitive():
    items = [_rec("r1", {"note": "Hello World"})]
    assert _client_fuzzy_filter(items, "hello") == items
    assert _client_fuzzy_filter(items, "HELLO") == items


def test_client_fuzzy_searches_nested_link_values():
    # SingleLink fields surface as [{record_ids, text, type}] — our matcher
    # should walk into dicts/lists, not just top-level strings.
    items = [
        _rec(
            "r1",
            {
                "related": [
                    {"record_ids": ["recA"], "text": "绑定订单 DJ-42"}
                ]
            },
        )
    ]
    assert _client_fuzzy_filter(items, "dj-42") == items


def test_client_fuzzy_matches_numeric_stringification():
    items = [_rec("r1", {"amount": 199})]
    assert _client_fuzzy_filter(items, "199") == items


def test_client_fuzzy_skips_nones():
    items = [_rec("r1", {"note": None, "name": "keep"})]
    assert _client_fuzzy_filter(items, "keep") == items
    assert _client_fuzzy_filter(items, "missing") == []


def test_client_fuzzy_no_match_returns_empty():
    items = [_rec("r1", {"name": "abc"})]
    assert _client_fuzzy_filter(items, "xyz") == []
