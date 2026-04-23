"""Unit tests for Link field auto-expansion."""

from __future__ import annotations

from typing import Any

import pytest

from feishu_super import schema as schema_module
from feishu_super.expand import BATCH_GET_CHUNK, expand_links
from feishu_super.schema import FieldMeta, TableSchema


class StubClient:
    """Minimal LarkClient stand-in for the code paths used by expand.

    Records every GET and POST for assertions about how many API calls the
    expansion made — used to verify idempotency and strategy selection
    (sparse batch_get vs dense list --all).
    """

    def __init__(self, get_responses: dict[str, dict[str, Any]]):
        # path → sequence of response dicts (we serve them in order)
        self._responses = {k: list(v) for k, v in get_responses.items()}
        self.get_calls: list[tuple[str, dict]] = []
        self.post_calls: list[tuple[str, Any]] = []

    def get(self, path: str, params: dict | None = None) -> dict:
        self.get_calls.append((path, dict(params or {})))
        if path in self._responses and self._responses[path]:
            return self._responses[path].pop(0)
        # Default: empty page
        return {"code": 0, "data": {"items": [], "has_more": False}}

    def post(self, path: str, json_body: Any = None, params: dict | None = None) -> dict:
        self.post_calls.append((path, json_body))
        if path in self._responses and self._responses[path]:
            return self._responses[path].pop(0)
        # Default: empty batch_get result
        return {"code": 0, "data": {"records": []}}


def _coach_schema(app_token: str, table_id: str, coach_target: str) -> TableSchema:
    """Schema with one Link field 教练 → coach table."""
    return TableSchema(
        table_id=table_id,
        by_name={
            "教练": FieldMeta(
                field_id="fld_coach",
                field_name="教练",
                type=18,
                property={"table_id": coach_target},
            ),
            "日期": FieldMeta(
                field_id="fld_date",
                field_name="日期",
                type=5,
                property={},
            ),
        },
        by_id={},
    )


def _coach_target_schema() -> TableSchema:
    return TableSchema(
        table_id="tbl_coach",
        by_name={
            "教练姓名": FieldMeta(
                field_id="fld_name",
                field_name="教练姓名",
                type=1,
                property={},
            )
        },
        by_id={},
    )


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    schema_module.clear_cache()
    yield
    schema_module.clear_cache()


def test_expand_short_shape_gets_text_and_linked_records():
    source_schema = _coach_schema("app", "tbl_src", "tbl_coach")
    # Simulate `records search` output: Link field in short shape.
    records = [
        {
            "record_id": "recA",
            "fields": {
                "教练": {"link_record_ids": ["rec_coach1"]},
                "日期": 1712345678000,
            },
        }
    ]
    # Sparse path: expand uses batch_get since |ref_ids|=1.
    batch_get_response = {
        "code": 0,
        "data": {
            "records": [
                {
                    "record_id": "rec_coach1",
                    "fields": {
                        "教练姓名": "田阳",
                        "工龄": 5,
                        "电话": "13800138000",
                    },
                },
            ],
        },
    }
    fields_list_response = {
        "code": 0,
        "data": {
            "items": [
                {
                    "field_id": "fld_name",
                    "field_name": "教练姓名",
                    "type": 1,
                    "property": {},
                }
            ],
            "has_more": False,
        },
    }
    client = StubClient(
        {
            "/bitable/v1/apps/app/tables/tbl_coach/records/batch_get": [batch_get_response],
            "/bitable/v1/apps/app/tables/tbl_coach/fields": [fields_list_response],
        }
    )

    result = expand_links(client, "app", records, source_schema)
    coach = result[0]["fields"]["教练"]
    assert isinstance(coach, list) and len(coach) == 1
    entry = coach[0]
    assert entry["text"] == "田阳"
    assert entry["text_arr"] == ["田阳"]
    assert entry["record_ids"] == ["rec_coach1"]
    assert entry["table_id"] == "tbl_coach"
    # NEW: full linked record fields accessible for downstream use.
    assert entry["linked_records"] == [
        {
            "record_id": "rec_coach1",
            "fields": {
                "教练姓名": "田阳",
                "工龄": 5,
                "电话": "13800138000",
            },
        }
    ]


def test_expand_idempotent_when_already_enriched():
    """Records that already have `linked_records` should cause zero API calls."""
    source_schema = _coach_schema("app", "tbl_src", "tbl_coach")
    records = [
        {
            "record_id": "recA",
            "fields": {
                "教练": [
                    {
                        "record_ids": ["rec_coach1"],
                        "table_id": "tbl_coach",
                        "text": "田阳",
                        "text_arr": ["田阳"],
                        "type": "text",
                        "linked_records": [
                            {"record_id": "rec_coach1", "fields": {"教练姓名": "田阳"}}
                        ],
                    }
                ]
            },
        }
    ]
    client = StubClient({})

    result = expand_links(client, "app", records, source_schema)
    assert client.get_calls == []
    # Record structure is unchanged.
    assert result[0]["fields"]["教练"][0]["text"] == "田阳"
    assert result[0]["fields"]["教练"][0]["linked_records"][0]["fields"]["教练姓名"] == "田阳"


def test_expand_upgrades_list_get_shape_to_linked():
    """Records from list/get (have text but no linked_records) should get
    enriched with linked_records — we DO fetch the target table."""
    source_schema = _coach_schema("app", "tbl_src", "tbl_coach")
    records = [
        {
            "record_id": "recA",
            "fields": {
                "教练": [
                    {
                        "record_ids": ["rec_coach1"],
                        "table_id": "tbl_coach",
                        "text": "田阳",
                        "text_arr": ["田阳"],
                        "type": "text",
                    }
                ]
            },
        }
    ]
    batch_get_response = {
        "code": 0,
        "data": {
            "records": [
                {
                    "record_id": "rec_coach1",
                    "fields": {"教练姓名": "田阳", "储值": 1500},
                }
            ],
        },
    }
    fields_response = {
        "code": 0,
        "data": {
            "items": [
                {"field_id": "fn", "field_name": "教练姓名", "type": 1, "property": {}}
            ],
            "has_more": False,
        },
    }
    client = StubClient(
        {
            "/bitable/v1/apps/app/tables/tbl_coach/records/batch_get": [batch_get_response],
            "/bitable/v1/apps/app/tables/tbl_coach/fields": [fields_response],
        }
    )
    result = expand_links(client, "app", records, source_schema)
    entry = result[0]["fields"]["教练"][0]
    assert "linked_records" in entry
    assert entry["linked_records"][0]["fields"]["储值"] == 1500


def test_expand_handles_empty_records():
    source_schema = _coach_schema("app", "tbl_src", "tbl_coach")
    client = StubClient({})
    result = expand_links(client, "app", [], source_schema)
    assert result == []
    assert client.get_calls == []


def test_expand_skips_when_no_link_fields():
    """Source table with no Link columns → pure no-op."""
    schema = TableSchema(
        table_id="tbl_x",
        by_name={
            "标题": FieldMeta("fld_title", "标题", 1, {}),
            "计数": FieldMeta("fld_count", "计数", 2, {}),
        },
        by_id={},
    )
    client = StubClient({})
    records = [{"record_id": "r1", "fields": {"标题": "x", "计数": 3}}]
    result = expand_links(client, "app", records, schema)
    assert result == records
    assert client.get_calls == []


def test_expand_groups_by_target_table():
    """Two Link columns pointing at different target tables → two fetches, not four."""
    source_schema = TableSchema(
        table_id="tbl_src",
        by_name={
            "教练": FieldMeta("fld_coach", "教练", 18, {"table_id": "tbl_coach"}),
            "服务": FieldMeta("fld_svc", "服务", 18, {"table_id": "tbl_svc"}),
        },
        by_id={},
    )
    records = [
        {
            "record_id": "recA",
            "fields": {
                "教练": {"link_record_ids": ["rec_c1"]},
                "服务": {"link_record_ids": ["rec_s1"]},
            },
        },
        {
            "record_id": "recB",
            "fields": {
                "教练": {"link_record_ids": ["rec_c2"]},
                "服务": {"link_record_ids": ["rec_s1"]},
            },
        },
    ]
    coach_batch = {
        "code": 0,
        "data": {
            "records": [
                {"record_id": "rec_c1", "fields": {"教练姓名": "田阳"}},
                {"record_id": "rec_c2", "fields": {"教练姓名": "李雷"}},
            ],
        },
    }
    svc_batch = {
        "code": 0,
        "data": {
            "records": [
                {"record_id": "rec_s1", "fields": {"服务名称": "力量训练"}},
            ],
        },
    }
    fields_coach = {
        "code": 0,
        "data": {
            "items": [{"field_id": "fn", "field_name": "教练姓名", "type": 1, "property": {}}],
            "has_more": False,
        },
    }
    fields_svc = {
        "code": 0,
        "data": {
            "items": [{"field_id": "fn", "field_name": "服务名称", "type": 1, "property": {}}],
            "has_more": False,
        },
    }
    client = StubClient(
        {
            "/bitable/v1/apps/app/tables/tbl_coach/records/batch_get": [coach_batch],
            "/bitable/v1/apps/app/tables/tbl_svc/records/batch_get": [svc_batch],
            "/bitable/v1/apps/app/tables/tbl_coach/fields": [fields_coach],
            "/bitable/v1/apps/app/tables/tbl_svc/fields": [fields_svc],
        }
    )

    result = expand_links(client, "app", records, source_schema)

    # Two target tables → exactly 2 batch_get calls (sparse path; each group's
    # ids fit in one chunk of ≤100). Plus schema fetches (one per target).
    batch_calls = [p for p, _ in client.post_calls if p.endswith("/records/batch_get")]
    assert len(batch_calls) == 2
    # And no fallback to list (|refs|=1 or 2, well under threshold).
    records_list_calls = [p for p, _ in client.get_calls if p.endswith("/records")]
    assert records_list_calls == []

    assert result[0]["fields"]["教练"][0]["text"] == "田阳"
    assert result[0]["fields"]["服务"][0]["text"] == "力量训练"
    assert result[1]["fields"]["教练"][0]["text"] == "李雷"
    assert result[1]["fields"]["服务"][0]["text"] == "力量训练"


def test_expand_only_whitelist_skips_other_link_fields():
    """When `only` is provided, only the named link fields get expanded;
    other link fields keep their original short shape (untouched)."""
    source_schema = TableSchema(
        table_id="tbl_src",
        by_name={
            "教练": FieldMeta("fld_coach", "教练", 18, {"table_id": "tbl_coach"}),
            "服务": FieldMeta("fld_svc", "服务", 18, {"table_id": "tbl_svc"}),
        },
        by_id={},
    )
    records = [
        {
            "record_id": "recA",
            "fields": {
                "教练": {"link_record_ids": ["rec_c1"]},
                "服务": {"link_record_ids": ["rec_s1"]},
            },
        }
    ]
    coach_batch = {
        "code": 0,
        "data": {
            "records": [{"record_id": "rec_c1", "fields": {"教练姓名": "田阳"}}],
        },
    }
    fields_coach = {
        "code": 0,
        "data": {
            "items": [{"field_id": "fn", "field_name": "教练姓名", "type": 1, "property": {}}],
            "has_more": False,
        },
    }
    client = StubClient(
        {
            "/bitable/v1/apps/app/tables/tbl_coach/records/batch_get": [coach_batch],
            "/bitable/v1/apps/app/tables/tbl_coach/fields": [fields_coach],
        }
    )

    result = expand_links(client, "app", records, source_schema, only={"教练"})

    # 教练 was expanded normally.
    assert result[0]["fields"]["教练"][0]["text"] == "田阳"
    assert result[0]["fields"]["教练"][0]["linked_records"][0]["fields"]["教练姓名"] == "田阳"
    # 服务 was NOT expanded — still in the original short shape.
    assert result[0]["fields"]["服务"] == {"link_record_ids": ["rec_s1"]}
    # And no API calls (get OR post) touched tbl_svc at all.
    svc_calls = (
        [p for p, _ in client.get_calls if "tbl_svc" in p]
        + [p for p, _ in client.post_calls if "tbl_svc" in p]
    )
    assert svc_calls == []


def test_expand_only_empty_set_is_treated_as_no_op():
    """`only=set()` — caller wanted to expand nothing — should be a no-op
    (no API calls, records unchanged)."""
    source_schema = _coach_schema("app", "tbl_src", "tbl_coach")
    records = [
        {
            "record_id": "recA",
            "fields": {"教练": {"link_record_ids": ["rec_c1"]}},
        }
    ]
    client = StubClient({})
    result = expand_links(client, "app", records, source_schema, only=set())
    assert client.get_calls == []
    assert client.post_calls == []
    assert result[0]["fields"]["教练"] == {"link_record_ids": ["rec_c1"]}


def test_expand_only_unknown_name_silently_skips():
    """expand_links itself doesn't validate names — that's the CLI layer's
    job (see test_records_cli). When `only` contains a name that isn't a
    link field on the schema, expand_links just skips everything."""
    source_schema = _coach_schema("app", "tbl_src", "tbl_coach")
    records = [
        {
            "record_id": "recA",
            "fields": {"教练": {"link_record_ids": ["rec_c1"]}},
        }
    ]
    client = StubClient({})
    result = expand_links(client, "app", records, source_schema, only={"不存在"})
    assert client.get_calls == []
    assert client.post_calls == []
    assert result[0]["fields"]["教练"] == {"link_record_ids": ["rec_c1"]}


def test_expand_missing_target_record_leaves_empty_text():
    """If a link points at a record_id that's not in the target table
    (deleted, permission, etc.), we should return empty text rather than
    crash."""
    source_schema = _coach_schema("app", "tbl_src", "tbl_coach")
    records = [
        {
            "record_id": "recA",
            "fields": {"教练": {"link_record_ids": ["rec_missing"]}},
        }
    ]
    # batch_get for rec_missing returns an empty records array (the id was
    # deleted / not visible). The sparse path must still produce an empty
    # linked_records entry for downstream uniformity.
    coach_batch = {
        "code": 0,
        "data": {"records": []},
    }
    fields_coach = {
        "code": 0,
        "data": {
            "items": [{"field_id": "fn", "field_name": "教练姓名", "type": 1, "property": {}}],
            "has_more": False,
        },
    }
    client = StubClient(
        {
            "/bitable/v1/apps/app/tables/tbl_coach/records/batch_get": [coach_batch],
            "/bitable/v1/apps/app/tables/tbl_coach/fields": [fields_coach],
        }
    )
    result = expand_links(client, "app", records, source_schema)
    coach = result[0]["fields"]["教练"]
    assert coach[0]["record_ids"] == ["rec_missing"]
    assert coach[0]["text_arr"] == [""]
    assert coach[0]["text"] == ""
    # Still includes a linked_records entry (empty fields) so downstream
    # code can uniformly iterate without special-casing missing refs.
    assert coach[0]["linked_records"] == [{"record_id": "rec_missing", "fields": {}}]


def test_sparse_path_uses_batch_get_and_passes_all_refs():
    """Sparse path: when |ref_ids| ≤ threshold, expand must call batch_get
    with the exact set of referenced ids — not scan the target table.

    Also verifies chunking: 150 ids → 2 batch_get calls (cap 100 per call).
    """
    source_schema = _coach_schema("app", "tbl_src", "tbl_coach")
    # 150 source records, each referencing a distinct coach id.
    records = [
        {
            "record_id": f"rec_src_{i}",
            "fields": {"教练": {"link_record_ids": [f"rec_c{i}"]}},
        }
        for i in range(150)
    ]
    # batch_get returns the records for each chunk.
    chunk1 = {
        "code": 0,
        "data": {
            "records": [
                {"record_id": f"rec_c{i}", "fields": {"教练姓名": f"name_{i}"}}
                for i in range(BATCH_GET_CHUNK)
            ]
        },
    }
    chunk2 = {
        "code": 0,
        "data": {
            "records": [
                {"record_id": f"rec_c{i}", "fields": {"教练姓名": f"name_{i}"}}
                for i in range(BATCH_GET_CHUNK, 150)
            ]
        },
    }
    fields_coach = {
        "code": 0,
        "data": {
            "items": [{"field_id": "fn", "field_name": "教练姓名", "type": 1, "property": {}}],
            "has_more": False,
        },
    }
    client = StubClient(
        {
            "/bitable/v1/apps/app/tables/tbl_coach/records/batch_get": [chunk1, chunk2],
            "/bitable/v1/apps/app/tables/tbl_coach/fields": [fields_coach],
        }
    )

    expand_links(client, "app", records, source_schema)

    # Exactly 2 batch_get calls (150 ids / 100 per chunk, rounded up).
    batch_calls = [p for p, _ in client.post_calls if p.endswith("/records/batch_get")]
    assert len(batch_calls) == 2
    # No list --all traffic at all.
    list_calls = [p for p, _ in client.get_calls if p.endswith("/records")]
    assert list_calls == []

    # Union of the two request bodies' record_ids covers all 150 refs.
    sent_ids: set[str] = set()
    for _, body in client.post_calls:
        sent_ids.update(body["record_ids"])
    assert sent_ids == {f"rec_c{i}" for i in range(150)}


def test_dense_path_falls_back_to_list(monkeypatch):
    """Dense path: when |ref_ids| exceeds the threshold, expand falls back to
    records/list --all (one call regardless of how many rows are referenced).
    We shrink the threshold to 2 so we don't need to build 1000 fake refs.
    """
    from feishu_super import expand as expand_module

    # Guard: if someone inlines the constant into `_should_use_sparse_path`,
    # monkeypatching the module attribute would silently stop testing the
    # fallback path. Assert the constant still exists at its documented value
    # before we patch it.
    assert expand_module.SPARSE_BATCH_GET_THRESHOLD == 1000
    monkeypatch.setattr(expand_module, "SPARSE_BATCH_GET_THRESHOLD", 2)

    source_schema = _coach_schema("app", "tbl_src", "tbl_coach")
    records = [
        {
            "record_id": f"rec_src_{i}",
            "fields": {"教练": {"link_record_ids": [f"rec_c{i}"]}},
        }
        for i in range(3)  # 3 refs > threshold=2 → dense path
    ]
    coach_list = {
        "code": 0,
        "data": {
            "items": [
                {"record_id": "rec_c0", "fields": {"教练姓名": "田阳"}},
                {"record_id": "rec_c1", "fields": {"教练姓名": "李雷"}},
                {"record_id": "rec_c2", "fields": {"教练姓名": "王芳"}},
            ],
            "has_more": False,
        },
    }
    fields_coach = {
        "code": 0,
        "data": {
            "items": [{"field_id": "fn", "field_name": "教练姓名", "type": 1, "property": {}}],
            "has_more": False,
        },
    }
    client = StubClient(
        {
            "/bitable/v1/apps/app/tables/tbl_coach/records": [coach_list],
            "/bitable/v1/apps/app/tables/tbl_coach/fields": [fields_coach],
        }
    )

    result = expand_links(client, "app", records, source_schema)

    # Dense path: exactly 1 GET on /records, zero POSTs on /batch_get.
    list_calls = [p for p, _ in client.get_calls if p.endswith("/records")]
    assert len(list_calls) == 1
    batch_calls = [p for p, _ in client.post_calls if p.endswith("/records/batch_get")]
    assert batch_calls == []

    # Correctness: all 3 refs still resolved via the list index.
    names = [r["fields"]["教练"][0]["text"] for r in result]
    assert names == ["田阳", "李雷", "王芳"]
