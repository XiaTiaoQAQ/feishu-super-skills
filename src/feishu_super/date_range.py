"""Date-semantic parameters for DateTime field filtering.

Feishu's `records/search` DOES NOT accept range operators
(`isGreater`/`isLess`/`isGreaterEqual`/`isLessEqual`) on DateTime fields —
it returns `code=1254018 InvalidFilter`. So range filtering MUST be done
locally. This module handles:

  1. Converting user-friendly flags like `--date-tomorrow --tz Asia/Shanghai`
     into `[start_ms, end_ms)` epoch-millisecond half-open intervals.
  2. Parsing `--date-on YYYY-MM-DD` and `--date-range START..END`.
  3. Filtering a records list in-memory by checking whether a DateTime
     field's value (which Feishu returns as epoch milliseconds) falls in
     the interval.

All timezone math uses `zoneinfo` (Python 3.11+ stdlib). Default zone is
Asia/Shanghai since that's what the original failing task required; users
can override with `--tz <IANA name>`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "Asia/Shanghai"

# Semantic shortcut → days offset from "today in tz"
_SEMANTIC_OFFSETS: dict[str, int] = {
    "today": 0,
    "tomorrow": 1,
    "yesterday": -1,
}


class DateRangeError(ValueError):
    """Raised for invalid date params — surface to the user cleanly."""


@dataclass(frozen=True)
class DateRange:
    """A half-open epoch-ms interval [start_ms, end_ms)."""
    start_ms: int
    end_ms: int
    label: str  # human-readable description for logs

    def contains(self, ms: int) -> bool:
        return self.start_ms <= ms < self.end_ms


def resolve_tz(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or DEFAULT_TZ)
    except ZoneInfoNotFoundError as e:
        raise DateRangeError(f"未知时区: {tz_name!r}") from e


def _day_bounds(d: date, tz: ZoneInfo) -> tuple[int, int]:
    """Return [start_ms, end_ms) of the local day `d` in `tz`."""
    start_dt = datetime.combine(d, time.min, tzinfo=tz)
    end_dt = start_dt + timedelta(days=1)
    return (
        int(start_dt.timestamp() * 1000),
        int(end_dt.timestamp() * 1000),
    )


def _parse_iso_or_ms(s: str, tz: ZoneInfo) -> tuple[int, str]:
    """Accept 'YYYY-MM-DD' (interpreted in tz) or raw epoch-ms integer string.

    Returns (epoch_ms_at_start_of_day_or_exact, normalized_label).
    """
    s = s.strip()
    if not s:
        raise DateRangeError("日期边界为空")
    if s.isdigit():
        return int(s), s
    try:
        d = date.fromisoformat(s)
    except ValueError as e:
        raise DateRangeError(f"日期格式错误: {s!r}，期望 YYYY-MM-DD 或毫秒时间戳") from e
    start_ms, _end_ms = _day_bounds(d, tz)
    return start_ms, s


def build_date_range(
    *,
    tz_name: str | None = None,
    on: str | None = None,
    range_spec: str | None = None,
    today: bool = False,
    tomorrow: bool = False,
    yesterday: bool = False,
) -> DateRange | None:
    """Resolve any combination of date flags into a single DateRange.

    Returns None if no date flag was given. Raises DateRangeError if the
    flags are inconsistent (e.g. --date-today AND --date-on).
    """
    semantic_set = [k for k, v in {"today": today, "tomorrow": tomorrow, "yesterday": yesterday}.items() if v]
    provided = [bool(on), bool(range_spec), bool(semantic_set)]
    if sum(provided) == 0:
        return None
    if sum(provided) > 1:
        raise DateRangeError(
            "日期参数互斥：--date-on / --date-range / --date-today/tomorrow/yesterday 只能选一个"
        )
    if len(semantic_set) > 1:
        raise DateRangeError(f"语义日期只能选一个，收到: {semantic_set}")

    tz = resolve_tz(tz_name)
    now_local = datetime.now(tz).date()

    if semantic_set:
        key = semantic_set[0]
        d = now_local + timedelta(days=_SEMANTIC_OFFSETS[key])
        start_ms, end_ms = _day_bounds(d, tz)
        return DateRange(start_ms, end_ms, f"{key} ({d.isoformat()})")

    if on:
        try:
            d = date.fromisoformat(on.strip())
        except ValueError as e:
            raise DateRangeError(f"--date-on 日期格式错误: {on!r}，期望 YYYY-MM-DD") from e
        start_ms, end_ms = _day_bounds(d, tz)
        return DateRange(start_ms, end_ms, f"on {d.isoformat()}")

    assert range_spec is not None
    if ".." not in range_spec:
        raise DateRangeError(f"--date-range 格式错误: {range_spec!r}，期望 START..END")
    left, right = range_spec.split("..", 1)
    start_ms, left_label = _parse_iso_or_ms(left, tz)
    # For end: treat a bare date as "through the END of that day" — i.e.
    # use the day's end boundary so `2026-04-11..2026-04-12` covers both
    # days inclusively. For explicit ms, treat as exclusive upper bound.
    right = right.strip()
    if right.isdigit():
        end_ms = int(right)
        right_label = right
    else:
        try:
            end_d = date.fromisoformat(right)
        except ValueError as e:
            raise DateRangeError(f"--date-range 右边界格式错误: {right!r}") from e
        _start, end_ms = _day_bounds(end_d, tz)
        right_label = right
    if end_ms <= start_ms:
        raise DateRangeError("--date-range: 结束时间必须晚于开始时间")
    return DateRange(start_ms, end_ms, f"range {left_label}..{right_label}")


def _extract_ms(value: Any) -> int | None:
    """Pull an epoch-ms integer out of a Feishu DateTime field value."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        # Tolerate stringified ms (some APIs wrap numeric in string).
        try:
            return int(value)
        except ValueError:
            return None
    return None


def filter_records_by_date(
    records: list[dict[str, Any]],
    field_name: str,
    date_range: DateRange,
) -> list[dict[str, Any]]:
    """Return the subset of `records` whose `fields[field_name]` ms ∈ range."""
    out: list[dict[str, Any]] = []
    for rec in records:
        fields = rec.get("fields") or {}
        ms = _extract_ms(fields.get(field_name))
        if ms is None:
            continue
        if date_range.contains(ms):
            out.append(rec)
    return out
