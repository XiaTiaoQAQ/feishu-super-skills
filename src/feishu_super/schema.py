"""Table schema helpers — fetch once per (client, app_token, table_id), reuse.

Several features depend on knowing the field types of a table *before* calling
the data endpoints:

- Link expansion needs `property.table_id` of each SingleLink/DuplexLink field
  to know which target table to fan out to.
- `--where` DSL type-checking needs to reject DateTime range operators early
  (Feishu rejects them with code=1254018 InvalidFilter otherwise).
- The date-semantic parameters (`--date-field`, `--date-tomorrow`, ...) need
  to confirm the target field is actually a DateTime (type=5).

Caching is process-local and unbounded but keyed strictly — a single CLI
invocation asks for the same table at most a handful of times, so a plain
dict is fine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from feishu_super.client import LarkClient
from feishu_super.commands.fields import _fetch_all_fields


@dataclass(frozen=True)
class FieldMeta:
    field_id: str
    field_name: str
    type: int
    property: dict[str, Any]

    @property
    def target_table_id(self) -> str | None:
        """For SingleLink/DuplexLink fields, the target table ID."""
        if self.type in (18, 21):
            tid = self.property.get("table_id")
            return str(tid) if tid else None
        return None


@dataclass(frozen=True)
class TableSchema:
    table_id: str
    by_name: dict[str, FieldMeta]
    by_id: dict[str, FieldMeta]

    def get(self, name_or_id: str) -> FieldMeta | None:
        return self.by_name.get(name_or_id) or self.by_id.get(name_or_id)

    def field_type(self, name_or_id: str) -> int | None:
        meta = self.get(name_or_id)
        return meta.type if meta else None


# key: (app_token, table_id). Schema is a property of the table, not of the
# client instance — caching by (app_token, table_id) lets the same CLI command
# share results across multiple LarkClient instances (records.py historically
# built one client for the schema probe and a second for the main request,
# which silently double-fetched fields). Tests that need isolation must call
# clear_cache() in fixtures.
_schema_cache: dict[tuple[str, str], TableSchema] = {}


def get_table_schema(
    client: LarkClient, app_token: str, table_id: str
) -> TableSchema:
    """Fetch (and cache) the schema of a Bitable table."""
    key = (app_token, table_id)
    cached = _schema_cache.get(key)
    if cached is not None:
        return cached

    items = _fetch_all_fields(client, app_token, table_id)
    by_name: dict[str, FieldMeta] = {}
    by_id: dict[str, FieldMeta] = {}
    for raw in items:
        meta = FieldMeta(
            field_id=str(raw.get("field_id", "")),
            field_name=str(raw.get("field_name", "")),
            type=int(raw.get("type", 0)),
            property=dict(raw.get("property") or {}),
        )
        if meta.field_name:
            by_name[meta.field_name] = meta
        if meta.field_id:
            by_id[meta.field_id] = meta

    schema = TableSchema(table_id=table_id, by_name=by_name, by_id=by_id)
    _schema_cache[key] = schema
    return schema


def clear_cache() -> None:
    """Clear the process-local schema cache (used in tests)."""
    _schema_cache.clear()
