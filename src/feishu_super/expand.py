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

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from feishu_super.client import LarkClient
from feishu_super.commands._common import chunked_post, paginate_all
from feishu_super.schema import TableSchema, get_table_schema

LINK_FIELD_TYPES: frozenset[int] = frozenset({18, 21})

# Cap concurrent target-table fetches. Empirically Feishu has no read rate
# limit at this level (8 concurrent search workers all returned 0 errors),
# but 4 is enough to saturate the wall-time bottleneck in any realistic
# expand scenario (1-6 distinct target tables) and leaves QPS budget for
# other concurrent operations the caller may do.
EXPAND_MAX_WORKERS = 4

# Feishu's records/batch_get accepts at most 100 record_ids per request.
BATCH_GET_CHUNK = 100

# Below this many referenced ids we fire one-or-a-few batch_get calls instead
# of scanning the full target table. Measured crossover: on a 5947-row target
# with 17 refs, batch_get = 0.9s vs list --all = 75s (86× speedup). With
# 1000 refs the two strategies are roughly tied (10 batch_get chunks ≈ 12
# list pages); beyond that, list --all wins on round-trip count.
SPARSE_BATCH_GET_THRESHOLD = 1000


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


def _should_use_sparse_path(ref_ids: set[str] | None) -> bool:
    """Policy: dispatch to batch_get only when we have a non-empty, small-enough id set.

    `None` means the caller didn't track refs — fall back to dense list.
    Empty set would mean "no refs to resolve," but the upstream step skips
    such targets entirely, so this branch is defensive.
    """
    return ref_ids is not None and 0 < len(ref_ids) <= SPARSE_BATCH_GET_THRESHOLD


def _batch_get_target_records(
    client: LarkClient,
    app_token: str,
    target_table_id: str,
    ids: list[str],
) -> list[dict[str, Any]]:
    """Fetch specific target records by id via POST .../records/batch_get.

    Returns the raw list of {record_id, fields} dicts. Respects the
    100-ids-per-request API cap.

    Multi-chunk calls run concurrently — a 500-ref fetch (5 chunks × ~0.9s
    serial) becomes ~1s wall time. Single-chunk calls skip the executor
    entirely to avoid its setup overhead.
    """
    path = f"/bitable/v1/apps/{app_token}/tables/{target_table_id}/records/batch_get"
    if len(ids) <= BATCH_GET_CHUNK:
        return chunked_post(
            client,
            path,
            ids,
            body_key="record_ids",
            response_key="records",
            chunk_size=BATCH_GET_CHUNK,
        )

    chunks: list[list[str]] = [
        ids[i : i + BATCH_GET_CHUNK] for i in range(0, len(ids), BATCH_GET_CHUNK)
    ]
    workers = min(len(chunks), EXPAND_MAX_WORKERS)

    def fetch(chunk: list[str]) -> list[dict[str, Any]]:
        return chunked_post(
            client,
            path,
            chunk,
            body_key="record_ids",
            response_key="records",
            chunk_size=BATCH_GET_CHUNK,
        )

    out: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for recs in ex.map(fetch, chunks):
            out.extend(recs)
    return out


def _build_target_index(
    client: LarkClient,
    app_token: str,
    target_table_id: str,
    ref_ids: set[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Return (record_id → target_record_dict) + the primary field name.

    `target_record_dict` is the FULL {record_id, fields} dict for that row
    in the target table, preserving every column. That's what lets callers
    reach arbitrary downstream columns (储值余额, 剩余次数, ...) without
    an extra API roundtrip.

    Two strategies:
      - SPARSE (ref_ids supplied and 0 < |ref_ids| ≤ SPARSE_BATCH_GET_THRESHOLD):
        call batch_get for those specific ids. Massive win when a narrow source
        query (e.g. yesterday's 18 records) references a tiny fraction of a
        large target table (e.g. 17 customers out of 5947).
      - DENSE (no ref_ids, or too many): fall back to records/list --all.
        Fewer round trips when a large share of the target table is needed.

    `ref_ids=None` or `ref_ids=set()` both route to the dense path — the
    former means "caller didn't track refs," the latter means "nothing
    referenced" (upstream usually skips such targets, but we tolerate it).
    """
    target_schema = get_table_schema(client, app_token, target_table_id)
    primary = _primary_field_name(target_schema)

    if _should_use_sparse_path(ref_ids):
        assert ref_ids is not None  # narrowed by _should_use_sparse_path
        items = _batch_get_target_records(
            client, app_token, target_table_id, list(ref_ids)
        )
    else:
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
    *,
    only: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Normalize and enrich Link fields in-place.

    Output shape: list of link entries, each carrying `record_ids`,
    `table_id`, `text`, `text_arr`, `type`, AND `linked_records` — the last
    being the full `{record_id, fields}` dict of each target row. Callers
    can reach arbitrary target-table columns without another API call.

    `only`: optional set of field names to restrict expansion to. When
    None, every SingleLink/DuplexLink field on the source table is expanded
    (the historical default). When set, only those listed link fields are
    enriched — other link fields are left in their original Feishu shape.
    The caller is responsible for validating that the names are real link
    fields; this function silently skips unknowns.

    Returns the same list (mutation is in-place for efficiency).
    """
    if not records:
        return records

    # Step 1: figure out which fields in the source table are Link-typed and
    # which target tables they point to. When `only` is provided we further
    # restrict to that whitelist — no point pulling target tables for fields
    # the caller said they don't need.
    link_fields: dict[str, str] = {}  # field_name → target_table_id
    for name, meta in schema.by_name.items():
        if meta.type not in LINK_FIELD_TYPES:
            continue
        if only is not None and name not in only:
            continue
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
    #
    # Multiple target tables are fetched concurrently — each table's pull is
    # ~13s for a few thousand rows and the API has no rate limit at this
    # level. The same LarkClient is shared across worker threads (httpx is
    # thread-safe; token refresh is RLock-protected). Hard-fail semantics:
    # if any worker raises (FeishuApiError, network, etc.), the executor
    # context cancels remaining futures and the exception propagates up.
    target_indexes: dict[str, dict[str, dict[str, Any]]] = {}
    target_primary: dict[str, str | None] = {}
    needed_ids = list(needed)
    if len(needed_ids) == 1:
        tid = needed_ids[0]
        index, primary = _build_target_index(
            client, app_token, tid, ref_ids=needed[tid]
        )
        target_indexes[tid] = index
        target_primary[tid] = primary
    else:
        workers = min(len(needed_ids), EXPAND_MAX_WORKERS)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {
                ex.submit(
                    _build_target_index, client, app_token, tid, needed[tid]
                ): tid
                for tid in needed_ids
            }
            try:
                for fut, tid in futures.items():
                    index, primary = fut.result()
                    target_indexes[tid] = index
                    target_primary[tid] = primary
            except BaseException:
                # Cancel any pending futures so we don't waste 10+ seconds
                # finishing work whose results we'll discard.
                for fut in futures:
                    fut.cancel()
                raise

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
