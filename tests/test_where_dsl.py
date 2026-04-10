"""Unit tests for the simplified --where DSL parser."""

from __future__ import annotations

import pytest

from feishu_super.where_dsl import DslError, build_fuzzy_filter, parse_sort, parse_where


def test_parse_where_simple_equality():
    result = parse_where("status = active")
    assert result == {
        "conjunction": "and",
        "conditions": [
            {"field_name": "status", "operator": "is", "value": ["active"]},
        ],
    }


def test_parse_where_contains_quoted():
    result = parse_where('name contains "张三"')
    assert result["conditions"][0] == {
        "field_name": "name",
        "operator": "contains",
        "value": ["张三"],
    }


def test_parse_where_and_chain():
    result = parse_where('name contains "abc" and status = active')
    assert result["conjunction"] == "and"
    assert len(result["conditions"]) == 2
    assert result["conditions"][1]["operator"] == "is"


def test_parse_where_or_chain():
    result = parse_where("a = 1 or b = 2 or c = 3")
    assert result["conjunction"] == "or"
    assert len(result["conditions"]) == 3


def test_parse_where_unary_is_empty():
    result = parse_where("notes is_empty")
    assert result["conditions"][0] == {
        "field_name": "notes",
        "operator": "isEmpty",
        "value": [],
    }


def test_parse_where_numeric_operator():
    result = parse_where("年龄 >= 18")
    assert result["conditions"][0]["operator"] == "isGreaterEqual"
    assert result["conditions"][0]["value"] == ["18"]


def test_parse_where_rejects_mixed_conjunction():
    with pytest.raises(DslError, match="and/or 混用"):
        parse_where("a = 1 and b = 2 or c = 3")


def test_parse_where_rejects_empty():
    with pytest.raises(DslError):
        parse_where("")


def test_parse_where_rejects_unknown_op():
    # '~' is not in the tokenizer's op set — should fail at tokenize step.
    with pytest.raises(DslError):
        parse_where("name ~ abc")


def test_parse_where_rejects_wrong_token_shape():
    # Two field names in a row → parser expects an operator after 'name'.
    with pytest.raises(DslError, match="操作符"):
        parse_where("name abc def")


def test_build_fuzzy_filter():
    f = build_fuzzy_filter("abc", ["name", "phone", "url"])
    assert f == {
        "conjunction": "or",
        "conditions": [
            {"field_name": "name", "operator": "contains", "value": ["abc"]},
            {"field_name": "phone", "operator": "contains", "value": ["abc"]},
            {"field_name": "url", "operator": "contains", "value": ["abc"]},
        ],
    }


def test_build_fuzzy_filter_empty_returns_none():
    assert build_fuzzy_filter("", ["a"]) is None
    assert build_fuzzy_filter("abc", []) is None


def test_parse_sort_simple():
    assert parse_sort("created_time desc") == [
        {"field_name": "created_time", "desc": True}
    ]


def test_parse_sort_multiple():
    assert parse_sort("name asc, created_time desc") == [
        {"field_name": "name", "desc": False},
        {"field_name": "created_time", "desc": True},
    ]


def test_parse_sort_default_asc():
    assert parse_sort("name") == [{"field_name": "name", "desc": False}]
