"""Link field auto-expansion — goes BEYOND Feishu's native Link resolution.

Feishu's `records/search` returns SingleLink / DuplexLink fields as a bare
`{"link_record_ids":["rec..."]}` — no text, no table_id. `records/list` and
`records/:id` give a slightly fuller
`{"record_ids":[...], "table_id":"tbl...", "text":"田阳", "text_arr":[...]}`
shape, but that's still only the PRIMARY field of the target table.

For a realistic operational task like "generate tomorrow's booking reminders"
the caller needs not only the primary field but ALSO arbitrary target-table
fields: customer's 储值余额, punch-card's 剩余次数, coach's 教练姓名, etc.
Without that, the caller has to fire one extra `records get` per linked
record, which is exactly the pain point the remote agent hit.

This module normalizes AND enriches. For each Link field it produces:

    [
      {
        "record_ids": ["rec..."],
        "table_id": "tbl...",
        "text": "田阳",           # primary-field text (compat with list/get)
        "text_arr": ["田阳"],      # same, multi-value
        "type": "text",
        "linked_records": [        # NEW: full fields of each referenced
          {                        # target record, so downstream code can
            "record_id": "rec...", # read any column without extra API calls
            "fields": { ... }
          }
        ]
      }
    ]

Idempotency: if a field already has `linked_records`, we skip it — running
expand twice never fires duplicate API calls.

Cost model: one search + M `records list --all` calls where M = number of
distinct target tables referenced. Each target table is fetched ONCE
regardless of how many records reference it. Per-table pulls are capped by
paginate_all's MAX_PAGES with a user-visible warning.

Scope: only SingleLink (18) and DuplexLink (21) are expanded. Lookup (19) /
Formula (20) are out of scope — their resolution rules differ and they can
form cycles.
"""

from __future__ import annotations

from typing import Any

from feishu_super.client import LarkClient
from feishu_super.commands._common import paginate_all
from feishu_super.schema import TableSchema, get_table_schema

LINK_FIELD_TYPES: frozenset[int] = frozenset({18, 21})


def _needs_expand(value: Any) -> bool:
    """Decide whether a Link field value still needs expansion.

    Three shapes we see in the wild:
      1. `{"link_record_ids":["rec..."]}` — search's short shape, must expand
      2. `[{"record_ids":[...], "text":"...", ...}]` — list/get shape, has
         text but NOT linked_records — we DO want to add linked_records
      3. `[{..., "linked_records":[{"record_id":"rec...", "fields":{...}}]}]`
         — already fully expanded by us previously, idempotent skip

    Any shape that lacks our custom `linked_records` key is a candidate for
    enrichment.
    """
    if isinstance(value, dict) and "link_record_ids" in value:
        return True
    if isinstance(value, list) and value:
        # list of link entries
        first = value[0]
        if isinstance(first, dict) and "linked_records" not in first:
            return True
    return False


def _extract_link_ids(value: Any) -> list[str]:
    """Pull referenced record_ids out of either shape 1 or shape 2."""
    if isinstance(value, dict):
        raw = value.get("link_record_ids") or []
        return [str(r) for r in raw if r]
    if isinstance(value, list):
        ids: list[str] = []
        for entry in value:
            if not isinstance(entry, dict):
                continue
            rids = entry.get("record_ids") or []
            ids.extend(str(r) for r in rids if r)
        return ids
    return []


def _primary_field_name(target_schema: TableSchema) -> str | None:
    """Best-effort: the first field of a Bitable table is the primary field.
    Fields come back from Feishu in display order, so by_name dict preserves
    insertion order (Python 3.7+).
    """
    for name in target_schema.by_name:
        return name
    return None


def _extract_text(raw_fields: dict[str, Any], primary: str | None) -> str:
    """Pull the primary-field text out of a target record's fields dict.

    Primary fields in Feishu are usually Text (returns `"..."`), but can also
    be a Formula/Lookup wrapping a string. Handle the common shapes.
    """
    if not primary:
        return ""
    val = raw_fields.get(primary)
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        # Text arrays: [{"type":"text","text":"..."}] or plain ["..."]
        parts: list[str] = []
        for el in val:
            if isinstance(el, str):
                parts.append(el)
            elif isinstance(el, dict):
                t = el.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "".join(parts)
    if isinstance(val, dict):
        t = val.get("text")
        if isinstance(t, str):
            return t
    return str(val)


def _build_target_index(
    client: LarkClient,
    app_token: str,
    target_table_id: str,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Return (record_id → target_record_dict) + the primary field name.

    `target_record_dict` is the FULL {record_id, fields} dict for that row
    in the target table, preserving every column. That's what lets callers
    reach arbitrary downstream columns (储值余额, 剩余次数, ...) without
    an extra API roundtrip.

    Uses `records/list --all` because list natively returns the complete
    shape. Capped by paginate_all's MAX_PAGES with a warning.
    """
    target_schema = get_table_schema(client, app_token, target_table_id)
    primary = _primary_field_name(target_schema)

    path = f"/bitable/v1/apps/{app_token}/tables/{target_table_id}/records"

    def fetch(pt: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": 500}
        if pt:
            params["page_token"] = pt
        return client.get(path, params=params).get("data") or {}

    items = paginate_all(
        fetch,
        fetch_all=True,
        max_pages=200,  # 500 × 200 = 100k-row ceiling per target table
        resource_label=f"records in {target_table_id}",
    )

    index: dict[str, dict[str, Any]] = {}
    for rec in items:
        rid = rec.get("record_id")
        if not rid:
            continue
        index[str(rid)] = {
            "record_id": str(rid),
            "fields": dict(rec.get("fields") or {}),
        }
    return index, primary


def expand_links(
    client: LarkClient,
    app_token: str,
    records: list[dict[str, Any]],
    schema: TableSchema,
) -> list[dict[str, Any]]:
    """Normalize and enrich Link fields in-place.

    Output shape: list of link entries, each carrying `record_ids`,
    `table_id`, `text`, `text_arr`, `type`, AND `linked_records` — the last
    being the full `{record_id, fields}` dict of each target row. Callers
    can reach arbitrary target-table columns without another API call.

    Returns the same list (mutation is in-place for efficiency).
    """
    if not records:
        return records

    # Step 1: figure out which fields in the source table are Link-typed and
    # which target tables they point to.
    link_fields: dict[str, str] = {}  # field_name → target_table_id
    for name, meta in schema.by_name.items():
        if meta.type in LINK_FIELD_TYPES:
            tid = meta.target_table_id
            if tid:
                link_fields[name] = tid

    if not link_fields:
        return records

    # Step 2: scan records, gather referenced record_ids grouped by target
    # table. A field is eligible if it lacks our `linked_records` key; that
    # keeps the operation idempotent across repeated expand calls.
    needed: dict[str, set[str]] = {}  # target_table_id → {record_ids}
    for rec in records:
        fields = rec.get("fields") or {}
        for fname, tid in link_fields.items():
            val = fields.get(fname)
            if not _needs_expand(val):
                continue
            ids = _extract_link_ids(val)
            if ids:
                needed.setdefault(tid, set()).update(ids)

    if not needed:
        return records

    # Step 3: for each target table, fetch once and build an index of
    # full records keyed by record_id, plus remember the primary field.
    target_indexes: dict[str, dict[str, dict[str, Any]]] = {}
    target_primary: dict[str, str | None] = {}
    for tid in needed:
        index, primary = _build_target_index(client, app_token, tid)
        target_indexes[tid] = index
        target_primary[tid] = primary

    # Step 4: rewrite each link field to include linked_records.
    for rec in records:
        fields = rec.get("fields") or {}
        for fname, tid in link_fields.items():
            val = fields.get(fname)
            if not _needs_expand(val):
                continue
            ids = _extract_link_ids(val)
            if not ids:
                continue
            index = target_indexes.get(tid) or {}
            primary = target_primary.get(tid)
            linked: list[dict[str, Any]] = []
            texts: list[str] = []
            for rid in ids:
                entry = index.get(rid)
                if entry is None:
                    linked.append({"record_id": rid, "fields": {}})
                    texts.append("")
                else:
                    linked.append(entry)
                    texts.append(_extract_text(entry.get("fields") or {}, primary))
            fields[fname] = [
                {
                    "record_ids": ids,
                    "table_id": tid,
                    "text": "".join(t for t in texts if t),
                    "text_arr": texts,
                    "type": "text",
                    "linked_records": linked,
                }
            ]

    return records
