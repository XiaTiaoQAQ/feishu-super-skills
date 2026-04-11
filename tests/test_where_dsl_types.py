"""Unit tests for the DateTime-aware guard in the --where DSL."""

from __future__ import annotations

import pytest

from feishu_super.where_dsl import DslError, parse_where


DATETIME_FIELD_TYPES = {"日期": 5, "name": 1, "amount": 2}


def test_datetime_greater_rejected():
    with pytest.raises(DslError, match="DateTime"):
        parse_where("日期 > 1712000000000", field_types=DATETIME_FIELD_TYPES)


def test_datetime_greater_equal_rejected():
    with pytest.raises(DslError, match="DateTime"):
        parse_where("日期 >= 1712000000000", field_types=DATETIME_FIELD_TYPES)


def test_datetime_less_rejected():
    with pytest.raises(DslError, match="DateTime"):
        parse_where("日期 < 1712000000000", field_types=DATETIME_FIELD_TYPES)


def test_datetime_less_equal_rejected():
    with pytest.raises(DslError, match="DateTime"):
        parse_where("日期 <= 1712000000000", field_types=DATETIME_FIELD_TYPES)


def test_datetime_equality_allowed():
    # `=` is the ONE range-ish operator that IS compatible with DateTime.
    r = parse_where("日期 = 1712000000000", field_types=DATETIME_FIELD_TYPES)
    assert r["conditions"][0]["operator"] == "is"


def test_datetime_is_empty_allowed():
    r = parse_where("日期 is_empty", field_types=DATETIME_FIELD_TYPES)
    assert r["conditions"][0]["operator"] == "isEmpty"


def test_non_datetime_range_allowed():
    # Number field should allow range operators normally.
    r = parse_where("amount > 100", field_types=DATETIME_FIELD_TYPES)
    assert r["conditions"][0]["operator"] == "isGreater"


def test_guard_disabled_without_field_types():
    # Backwards compat: if caller doesn't supply field_types, guard is silent.
    r = parse_where("日期 > 1712000000000")
    assert r["conditions"][0]["operator"] == "isGreater"


def test_guard_ignores_unknown_fields():
    # Field not in the map → no type info → no guard.
    r = parse_where("unknown > 100", field_types=DATETIME_FIELD_TYPES)
    assert r["conditions"][0]["operator"] == "isGreater"


def test_guard_error_mentions_fix():
    with pytest.raises(DslError) as exc:
        parse_where("日期 > 123", field_types=DATETIME_FIELD_TYPES)
    # The error should steer the user toward the semantic flags.
    msg = str(exc.value)
    assert "--date-range" in msg or "date-" in msg
