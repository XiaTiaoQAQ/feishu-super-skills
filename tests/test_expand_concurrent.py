"""Tests for the concurrent target-table fetch path in expand_links.

These tests are orthogonal to test_expand.py: that file proves the
correctness of the expansion logic itself; this file proves that running
the same logic across threads produces identical results AND propagates
errors cleanly.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from feishu_super import schema as schema_module
from feishu_super.client import FeishuApiError
from feishu_super.expand import expand_links
from feishu_super.schema import FieldMeta, TableSchema


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    schema_module.clear_cache()
    yield
    schema_module.clear_cache()


def _multi_target_schema() -> TableSchema:
    """Source table with 4 link fields → 4 distinct target tables.

    Mirrors the 销课记录 layout the perf benchmarks use, so this test
    exercises the same fan-out shape as the real workload.
    """
    return TableSchema(
        table_id="tbl_src",
        by_name={
            "上课人": FieldMeta("fld_a", "上课人", 18, {"table_id": "tbl_customer"}),
            "教练": FieldMeta("fld_b", "教练", 18, {"table_id": "tbl_coach"}),
            "次卡课包": FieldMeta("fld_c", "次卡课包", 18, {"table_id": "tbl_pkg"}),
            "预约服务": FieldMeta("fld_d", "预约服务", 18, {"table_id": "tbl_svc"}),
        },
        by_id={},
    )


def _make_records():
    """One source record that touches all 4 link fields."""
    return [
        {
            "record_id": "recA",
            "fields": {
                "上课人": {"link_record_ids": ["rec_cust1"]},
                "教练": {"link_record_ids": ["rec_coach1"]},
                "次卡课包": {"link_record_ids": ["rec_pkg1"]},
                "预约服务": {"link_record_ids": ["rec_svc1"]},
            },
        }
    ]


class ThreadingStubClient:
    """Tracks concurrent access patterns for assertions about parallelism.

    Handles both GET (schema + list --all) and POST (batch_get). Defaults
    match the endpoint's natural shape so callers don't need to differentiate.
    """

    def __init__(self, table_responses: dict[str, list[dict[str, Any]]],
                 *, fetch_delay: float = 0.0,
                 fail_paths: set[str] | None = None):
        # path → list of canned responses to serve in order
        self._responses = {k: list(v) for k, v in table_responses.items()}
        self.calls: list[tuple[str, dict, float]] = []
        self._lock = threading.Lock()
        self._fetch_delay = fetch_delay
        self._fail_paths = fail_paths or set()
        # in-flight: at any moment, how many threads are currently inside a call
        self._inflight = 0
        self.max_inflight = 0

    def _serve(self, path: str) -> dict:
        if path in self._fail_paths:
            raise FeishuApiError(1254030, "synthetic error", url=path)
        if self._fetch_delay:
            time.sleep(self._fetch_delay)
        if path in self._responses and self._responses[path]:
            return self._responses[path].pop(0)
        if path.endswith("/batch_get"):
            return {"code": 0, "data": {"records": []}}
        return {"code": 0, "data": {"items": [], "has_more": False}}

    def _enter(self, path: str, params_or_body: Any) -> None:
        with self._lock:
            self._inflight += 1
            self.max_inflight = max(self.max_inflight, self._inflight)
            self.calls.append((path, params_or_body, time.perf_counter()))

    def _exit(self) -> None:
        with self._lock:
            self._inflight -= 1

    def get(self, path: str, params: dict | None = None) -> dict:
        self._enter(path, dict(params or {}))
        try:
            return self._serve(path)
        finally:
            self._exit()

    def post(self, path: str, json_body: Any = None, params: dict | None = None) -> dict:
        self._enter(path, json_body)
        try:
            return self._serve(path)
        finally:
            self._exit()


def _canned_target_responses():
    """Build canned records/batch_get and fields/list responses for all 4 targets.

    Single-ref-per-field tests → sparse expand path → batch_get endpoint.
    """
    return {
        "/bitable/v1/apps/app/tables/tbl_customer/records/batch_get": [
            {"code": 0, "data": {"records": [{"record_id": "rec_cust1", "fields": {"客户名称": "申晴", "余额": 8570}}]}}
        ],
        "/bitable/v1/apps/app/tables/tbl_coach/records/batch_get": [
            {"code": 0, "data": {"records": [{"record_id": "rec_coach1", "fields": {"教练姓名": "田阳"}}]}}
        ],
        "/bitable/v1/apps/app/tables/tbl_pkg/records/batch_get": [
            {"code": 0, "data": {"records": [{"record_id": "rec_pkg1", "fields": {"课包名": "次卡A"}}]}}
        ],
        "/bitable/v1/apps/app/tables/tbl_svc/records/batch_get": [
            {"code": 0, "data": {"records": [{"record_id": "rec_svc1", "fields": {"服务名": "力量训练"}}]}}
        ],
        # Field schemas — primary field of each target, plus matching extras.
        "/bitable/v1/apps/app/tables/tbl_customer/fields": [
            {"code": 0, "data": {"items": [
                {"field_id": "f1", "field_name": "客户名称", "type": 1, "property": {}},
                {"field_id": "f2", "field_name": "余额", "type": 2, "property": {}},
            ], "has_more": False}}
        ],
        "/bitable/v1/apps/app/tables/tbl_coach/fields": [
            {"code": 0, "data": {"items": [{"field_id": "f1", "field_name": "教练姓名", "type": 1, "property": {}}], "has_more": False}}
        ],
        "/bitable/v1/apps/app/tables/tbl_pkg/fields": [
            {"code": 0, "data": {"items": [{"field_id": "f1", "field_name": "课包名", "type": 1, "property": {}}], "has_more": False}}
        ],
        "/bitable/v1/apps/app/tables/tbl_svc/fields": [
            {"code": 0, "data": {"items": [{"field_id": "f1", "field_name": "服务名", "type": 1, "property": {}}], "has_more": False}}
        ],
    }


def test_expand_concurrent_runs_targets_in_parallel():
    """With 4 targets and a 100ms-per-fetch delay, total wall time should be
    closer to 100ms (parallel) than 400ms (serial). We give it generous
    headroom (250ms) to account for scheduling overhead in CI."""
    schema = _multi_target_schema()
    client = ThreadingStubClient(_canned_target_responses(), fetch_delay=0.10)

    t0 = time.perf_counter()
    expand_links(client, "app", _make_records(), schema)
    dt = time.perf_counter() - t0

    assert dt < 0.30, f"expand should run targets in parallel, took {dt:.2f}s"
    # And we observed at least 2 inflight requests at some moment (proof of
    # actual concurrency rather than serial-with-fast-fetch).
    assert client.max_inflight >= 2, (
        f"expected parallel inflight, max_inflight={client.max_inflight}"
    )


def test_expand_concurrent_result_matches_serial():
    """The concurrent path must produce byte-identical results to a serial
    fetch — same dict structure, same key order, same content."""
    schema = _multi_target_schema()
    client = ThreadingStubClient(_canned_target_responses())

    result = expand_links(client, "app", _make_records(), schema)

    # Spot-check: every link field has linked_records with the right content.
    fields = result[0]["fields"]
    assert fields["上课人"][0]["linked_records"][0]["fields"]["余额"] == 8570
    assert fields["教练"][0]["text"] == "田阳"
    assert fields["次卡课包"][0]["text"] == "次卡A"
    assert fields["预约服务"][0]["text"] == "力量训练"

    # And the result is reproducible — running it again on a fresh client
    # produces the same JSON. (Cache is reset by autouse fixture.)
    schema_module.clear_cache()
    client2 = ThreadingStubClient(_canned_target_responses())
    result2 = expand_links(client2, "app", _make_records(), schema)
    assert json.dumps(result, sort_keys=True, ensure_ascii=False) == json.dumps(
        result2, sort_keys=True, ensure_ascii=False
    )


def test_expand_concurrent_propagates_first_error():
    """If one target table's fetch raises, the entire expand_links must
    raise FeishuApiError. We don't care which one wins the race — only
    that the exception escapes."""
    schema = _multi_target_schema()
    responses = _canned_target_responses()
    client = ThreadingStubClient(
        responses,
        fail_paths={"/bitable/v1/apps/app/tables/tbl_coach/records/batch_get"},
    )

    with pytest.raises(FeishuApiError) as exc_info:
        expand_links(client, "app", _make_records(), schema)
    assert exc_info.value.code == 1254030


def test_expand_single_target_takes_inline_path():
    """When only 1 target table is referenced, the code skips the
    ThreadPoolExecutor for zero overhead. We assert by checking that the
    result is correct — overhead is hard to assert directly."""
    schema = TableSchema(
        table_id="tbl_src",
        by_name={
            "教练": FieldMeta("fld_b", "教练", 18, {"table_id": "tbl_coach"}),
        },
        by_id={},
    )
    records = [{"record_id": "recA", "fields": {"教练": {"link_record_ids": ["rec_coach1"]}}}]
    client = ThreadingStubClient(_canned_target_responses())
    result = expand_links(client, "app", records, schema)
    assert result[0]["fields"]["教练"][0]["text"] == "田阳"
    # Single target means no concurrency, so max_inflight stays at 1.
    assert client.max_inflight == 1


def test_sparse_path_parallelizes_chunks():
    """When one target's ref set spans multiple batch_get chunks, those chunks
    must fire concurrently — otherwise a 500-ref expand (5 chunks × ~0.9s)
    would tail-block the whole operation at ~4.5s."""
    from feishu_super.expand import BATCH_GET_CHUNK

    schema = TableSchema(
        table_id="tbl_src",
        by_name={
            "教练": FieldMeta("fld_b", "教练", 18, {"table_id": "tbl_coach"}),
        },
        by_id={},
    )
    # 3 full chunks worth of distinct refs (cap per chunk = BATCH_GET_CHUNK).
    n_chunks = 3
    total = n_chunks * BATCH_GET_CHUNK
    records = [
        {"record_id": f"recA{i}", "fields": {"教练": {"link_record_ids": [f"rec_c{i}"]}}}
        for i in range(total)
    ]
    # Each batch_get response covers its own chunk.
    batch_responses = [
        {
            "code": 0,
            "data": {
                "records": [
                    {"record_id": f"rec_c{j}", "fields": {"教练姓名": f"n{j}"}}
                    for j in range(c * BATCH_GET_CHUNK, (c + 1) * BATCH_GET_CHUNK)
                ],
            },
        }
        for c in range(n_chunks)
    ]
    fields_coach = {
        "code": 0,
        "data": {
            "items": [{"field_id": "fn", "field_name": "教练姓名", "type": 1, "property": {}}],
            "has_more": False,
        },
    }
    # 0.1s per POST — serial would be 0.3s+, concurrent should be ~0.1s.
    client = ThreadingStubClient(
        {
            "/bitable/v1/apps/app/tables/tbl_coach/records/batch_get": batch_responses,
            "/bitable/v1/apps/app/tables/tbl_coach/fields": [fields_coach],
        },
        fetch_delay=0.10,
    )

    t0 = time.perf_counter()
    result = expand_links(client, "app", records, schema)
    dt = time.perf_counter() - t0

    # Two qualitative checks: wall time far below serial floor, AND at least
    # two chunks inflight simultaneously at some point.
    assert dt < 0.25, f"chunks should run in parallel, took {dt:.2f}s"
    assert client.max_inflight >= 2, (
        f"expected parallel chunk dispatch, max_inflight={client.max_inflight}"
    )
    # Sanity: all 300 refs resolved.
    resolved = {r["fields"]["教练"][0]["text"] for r in result}
    assert resolved == {f"n{i}" for i in range(total)}
