"""Unit tests for date-semantic parameter parsing and local filtering."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from feishu_super.date_range import (
    DateRange,
    DateRangeError,
    build_date_range,
    filter_records_by_date,
)


def _ms(y: int, m: int, d: int, tz: str = "Asia/Shanghai") -> int:
    return int(datetime(y, m, d, tzinfo=ZoneInfo(tz)).timestamp() * 1000)


def test_date_on_basic():
    r = build_date_range(on="2026-04-11", tz_name="Asia/Shanghai")
    assert r is not None
    assert r.start_ms == _ms(2026, 4, 11)
    assert r.end_ms == _ms(2026, 4, 12)


def test_date_on_invalid_format():
    with pytest.raises(DateRangeError, match="YYYY-MM-DD"):
        build_date_range(on="2026/04/11")


def test_date_range_two_iso_dates():
    r = build_date_range(range_spec="2026-04-11..2026-04-12", tz_name="Asia/Shanghai")
    assert r is not None
    # Inclusive of both days → [start of 4/11, end of 4/12) = [start of 4/11, start of 4/13)
    assert r.start_ms == _ms(2026, 4, 11)
    assert r.end_ms == _ms(2026, 4, 13)


def test_date_range_missing_separator():
    with pytest.raises(DateRangeError, match="START..END"):
        build_date_range(range_spec="2026-04-11")


def test_date_range_end_before_start():
    with pytest.raises(DateRangeError, match="结束时间"):
        build_date_range(range_spec="2026-04-12..2026-04-11")


def test_date_range_raw_ms():
    r = build_date_range(range_spec="1000000..2000000")
    assert r is not None
    assert r.start_ms == 1_000_000
    assert r.end_ms == 2_000_000


def test_semantic_tomorrow_crosses_midnight():
    r = build_date_range(tomorrow=True, tz_name="Asia/Shanghai")
    assert r is not None
    # Tomorrow's interval is exactly 24 hours wide.
    assert r.end_ms - r.start_ms == 24 * 3600 * 1000
    # Start should be midnight in Shanghai.
    start_dt = datetime.fromtimestamp(r.start_ms / 1000, tz=ZoneInfo("Asia/Shanghai"))
    assert start_dt.hour == 0 and start_dt.minute == 0
    # And it should be strictly after "today" start.
    today_r = build_date_range(today=True, tz_name="Asia/Shanghai")
    assert r.start_ms == today_r.end_ms


def test_semantic_yesterday_before_today():
    today_r = build_date_range(today=True, tz_name="Asia/Shanghai")
    yest_r = build_date_range(yesterday=True, tz_name="Asia/Shanghai")
    assert yest_r.end_ms == today_r.start_ms


def test_mutually_exclusive_params():
    with pytest.raises(DateRangeError, match="互斥"):
        build_date_range(today=True, on="2026-04-11")


def test_multiple_semantic_shortcuts():
    with pytest.raises(DateRangeError, match="语义日期只能选一个"):
        build_date_range(today=True, tomorrow=True)


def test_bad_tz():
    with pytest.raises(DateRangeError, match="未知时区"):
        build_date_range(today=True, tz_name="Atlantis/Atlantica")


def test_no_params_returns_none():
    assert build_date_range() is None


def test_different_tz_shifts_interval():
    # A "2026-04-11 in UTC" starts 8h earlier in ms than "2026-04-11 in Shanghai".
    r_utc = build_date_range(on="2026-04-11", tz_name="UTC")
    r_sh = build_date_range(on="2026-04-11", tz_name="Asia/Shanghai")
    assert r_utc.start_ms - r_sh.start_ms == 8 * 3600 * 1000


def test_filter_records_basic():
    r = DateRange(start_ms=100, end_ms=200, label="test")
    recs = [
        {"record_id": "r1", "fields": {"date": 50}},   # before
        {"record_id": "r2", "fields": {"date": 100}},  # inclusive start
        {"record_id": "r3", "fields": {"date": 150}},  # inside
        {"record_id": "r4", "fields": {"date": 200}},  # exclusive end
        {"record_id": "r5", "fields": {"date": 250}},  # after
        {"record_id": "r6", "fields": {}},             # missing field
    ]
    kept = filter_records_by_date(recs, "date", r)
    assert [r["record_id"] for r in kept] == ["r2", "r3"]


def test_filter_records_accepts_stringified_ms():
    r = DateRange(start_ms=100, end_ms=200, label="test")
    recs = [{"record_id": "r1", "fields": {"date": "150"}}]
    assert len(filter_records_by_date(recs, "date", r)) == 1


def test_filter_records_skips_non_numeric():
    r = DateRange(start_ms=100, end_ms=200, label="test")
    recs = [
        {"record_id": "r1", "fields": {"date": "not-a-number"}},
        {"record_id": "r2", "fields": {"date": None}},
    ]
    assert filter_records_by_date(recs, "date", r) == []
