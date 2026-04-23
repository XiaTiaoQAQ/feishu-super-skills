"""CLI-level tests for records subcommands.

Focused on the validation paths added by --expand-only:
- mutex with --no-expand
- field name must exist on the table
- field type must be SingleLink/DuplexLink (18 / 21)

These tests stub LarkClient + schema + paginate so no network is touched.
The runner uses typer.testing.CliRunner so the validation runs through the
real Typer wiring and exit codes are observed exactly as a user would see
them.
"""
from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from feishu_super import schema as schema_module
from feishu_super.cli import app as cli_app
from feishu_super.commands import records as records_cmd
from feishu_super.schema import FieldMeta, TableSchema


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Hermetic env: clean shell vars, isolate skill .env, clear schema cache."""
    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_APP_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    from feishu_super import config as _config
    monkeypatch.setattr(_config, "SKILL_ROOT", tmp_path)
    schema_module.clear_cache()
    yield
    schema_module.clear_cache()


def _stub_schema_with_link():
    """Sales-table-like schema: one Link field 上课人 + one Text field 备注."""
    return TableSchema(
        table_id="tbl_src",
        by_name={
            "上课人": FieldMeta("fld_a", "上课人", 18, {"table_id": "tbl_customer"}),
            "备注": FieldMeta("fld_b", "备注", 1, {}),
        },
        by_id={},
    )


@pytest.fixture
def stub_schema_fetch(monkeypatch):
    """Replace get_table_schema with a no-op that returns a known schema,
    so validation tests don't hit the network."""
    schema = _stub_schema_with_link()

    def fake_get(client, app_token, table_id):
        return schema

    monkeypatch.setattr(records_cmd, "get_table_schema", fake_get)
    return schema


@pytest.fixture
def stub_build_client(monkeypatch):
    """build_client returns an object with __enter__/__exit__ but never
    actually executes any HTTP. Validation paths must reject before any
    real client is needed."""
    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    monkeypatch.setattr(records_cmd, "build_client", lambda ctx: FakeClient())
    return FakeClient


def _runner_invoke(*args: str):
    runner = CliRunner()
    return runner.invoke(
        cli_app,
        [
            "--app-id", "cli_test",
            "--app-secret", "secret_test",
            "--app-token-default", "app_test",
            *args,
        ],
    )


def test_list_no_expand_and_expand_only_are_mutually_exclusive(stub_schema_fetch, stub_build_client):
    result = _runner_invoke(
        "records", "list", "tbl_src",
        "--no-expand", "--expand-only", "上课人",
    )
    assert result.exit_code == 2
    assert "互斥" in result.stderr


def test_list_expand_only_unknown_field_fails_fast(stub_schema_fetch, stub_build_client):
    result = _runner_invoke(
        "records", "list", "tbl_src",
        "--expand-only", "不存在",
    )
    assert result.exit_code == 2
    assert "不存在" in result.stderr


def test_list_expand_only_non_link_field_fails_fast(stub_schema_fetch, stub_build_client):
    # 备注 is type=1 (Text), not 18/21 → must be rejected.
    result = _runner_invoke(
        "records", "list", "tbl_src",
        "--expand-only", "备注",
    )
    assert result.exit_code == 2
    assert "Link" in result.stderr or "link" in result.stderr


def test_search_expand_only_validates_via_pre_pulled_schema(stub_schema_fetch, stub_build_client):
    # search uses its own validation block (re-uses already-pulled schema
    # when --where/--date-field is also present). Verify the unknown-field
    # path triggers there too.
    result = _runner_invoke(
        "records", "search", "tbl_src",
        "--expand-only", "鬼字段",
    )
    assert result.exit_code == 2
    assert "鬼字段" in result.stderr


def test_search_expand_only_non_link_field_fails(stub_schema_fetch, stub_build_client):
    result = _runner_invoke(
        "records", "search", "tbl_src",
        "--expand-only", "备注",
    )
    assert result.exit_code == 2


def test_search_expand_only_mutex_with_no_expand(stub_schema_fetch, stub_build_client):
    result = _runner_invoke(
        "records", "search", "tbl_src",
        "--no-expand", "--expand-only", "上课人",
    )
    assert result.exit_code == 2
    assert "互斥" in result.stderr


def test_get_expand_only_unknown_field_fails(stub_schema_fetch, stub_build_client):
    result = _runner_invoke(
        "records", "get", "tbl_src", "rec123",
        "--expand-only", "鬼字段",
    )
    assert result.exit_code == 2
    assert "鬼字段" in result.stderr


def test_parse_expand_only_helper_handles_whitespace_and_empty():
    """The internal helper trims whitespace and drops empty pieces."""
    from feishu_super.commands.records import _parse_expand_only

    assert _parse_expand_only(None) is None
    assert _parse_expand_only("") is None
    assert _parse_expand_only("a, b ,, c") == {"a", "b", "c"}
    assert _parse_expand_only("only_one") == {"only_one"}


# ---------------------------------------------------------------------------
# --date-* server-side filter routing
# ---------------------------------------------------------------------------


def _stub_schema_with_date():
    return TableSchema(
        table_id="tbl_src",
        by_name={
            "日期": FieldMeta("fld_d", "日期", 5, {}),   # DateTime
            "状态": FieldMeta("fld_s", "状态", 1, {}),   # Text
        },
        by_id={},
    )


@pytest.fixture
def stub_date_schema(monkeypatch):
    schema = _stub_schema_with_date()
    monkeypatch.setattr(records_cmd, "get_table_schema", lambda c, a, t: schema)
    return schema


@pytest.fixture
def recording_client(monkeypatch):
    """FakeClient that captures every POST (path, body) so tests can assert
    exactly what was sent to Feishu. Every POST returns an empty single
    page so paginate_all terminates immediately."""

    class Recorder:
        def __init__(self):
            self.posts: list[tuple[str, Any, dict]] = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def post(self, path, json_body=None, params=None):
            self.posts.append((path, json_body, dict(params or {})))
            return {"code": 0, "data": {"items": [], "has_more": False}}

        def get(self, path, params=None):
            return {"code": 0, "data": {"items": [], "has_more": False}}

    rec = Recorder()
    monkeypatch.setattr(records_cmd, "build_client", lambda ctx: rec)
    return rec


def test_date_range_goes_server_side_without_user_filter(stub_date_schema, recording_client):
    result = _runner_invoke(
        "records", "search", "tbl_src",
        "--date-field", "日期", "--date-on", "2026-04-22",
        "--tz", "Asia/Shanghai", "--show", "0", "--no-expand",
    )
    assert result.exit_code == 0, result.stderr

    # Exactly one POST to /records/search.
    search_posts = [p for p in recording_client.posts if p[0].endswith("/records/search")]
    assert len(search_posts) == 1
    _path, body, _params = search_posts[0]

    # body.filter must contain isGreater + isLess on the date field with
    # ExactDate tagged values — this is the server-side path.
    f = body["filter"]
    assert f["conjunction"] == "and"
    ops = {c["operator"] for c in f["conditions"]}
    assert ops == {"isGreater", "isLess"}
    for c in f["conditions"]:
        assert c["field_name"] == "日期"
        assert c["value"][0] == "ExactDate"
        assert c["value"][1].isdigit()  # millisecond string

    # Server-side path must NOT emit the client-side fallback warning.
    assert "回退客户端过滤" not in result.stderr
    assert "客户端筛选" not in result.stderr


def test_date_range_merges_with_and_where(stub_date_schema, recording_client):
    result = _runner_invoke(
        "records", "search", "tbl_src",
        "--where", '状态 = "active"',
        "--date-field", "日期", "--date-on", "2026-04-22",
        "--tz", "Asia/Shanghai", "--show", "0", "--no-expand",
    )
    assert result.exit_code == 0, result.stderr

    search_posts = [p for p in recording_client.posts if p[0].endswith("/records/search")]
    body = search_posts[0][1]
    f = body["filter"]
    assert f["conjunction"] == "and"
    # 1 user condition (状态 = active) + 2 date conditions = 3 total.
    assert len(f["conditions"]) == 3
    field_ops = [(c["field_name"], c["operator"]) for c in f["conditions"]]
    assert ("状态", "is") in field_ops
    assert ("日期", "isGreater") in field_ops
    assert ("日期", "isLess") in field_ops


def test_date_range_falls_back_to_client_on_or_filter(stub_date_schema, recording_client):
    result = _runner_invoke(
        "records", "search", "tbl_src",
        "--where", '状态 = "active" or 状态 = "done"',
        "--date-field", "日期", "--date-on", "2026-04-22",
        "--tz", "Asia/Shanghai", "--show", "0", "--no-expand",
    )
    assert result.exit_code == 0, result.stderr

    # Fallback path: user filter stays as top-level OR, date conditions NOT merged.
    search_posts = [p for p in recording_client.posts if p[0].endswith("/records/search")]
    assert len(search_posts) >= 1
    body = search_posts[0][1]
    f = body["filter"]
    assert f["conjunction"] == "or"
    # No date conditions injected server-side.
    for c in f["conditions"]:
        assert c["field_name"] != "日期"

    # stderr must flag the fallback + the auto-enabled --all.
    assert "回退客户端过滤" in result.stderr
    assert "--all" in result.stderr
