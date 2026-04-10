"""Unit tests for field type lookups."""

from __future__ import annotations

from feishu_super.field_types import is_read_only, is_text_like, type_name


def test_type_name_known():
    assert type_name(1) == "Text"
    assert type_name(5) == "DateTime"
    assert type_name(18) == "SingleLink"
    assert type_name(1001) == "CreatedTime"


def test_type_name_unknown():
    assert "Unknown" in type_name(9999)


def test_text_like():
    assert is_text_like(1)
    assert is_text_like(13)
    assert is_text_like(15)
    assert not is_text_like(2)
    assert not is_text_like(18)


def test_read_only():
    assert is_read_only(19)  # Lookup
    assert is_read_only(20)  # Formula
    assert is_read_only(1001)
    assert is_read_only(1005)
    assert not is_read_only(1)
    assert not is_read_only(18)
