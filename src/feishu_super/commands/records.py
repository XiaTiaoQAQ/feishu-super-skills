"""Record-level commands: list / get / search / create / update / delete + batch."""

from __future__ import annotations

import json
from typing import Any, Optional

import typer

from feishu_super.client import LarkClient
from feishu_super.commands._common import (
    build_client,
    handle_api_error,
    load_json_arg,
    paginate_all,
    resolve_app_token,
)
from feishu_super.commands.fields import _fetch_all_fields
from feishu_super.field_types import is_text_like
from feishu_super.formatters import emit_json, emit_warn, summarize_records
from feishu_super.guard import guard_write
from feishu_super.where_dsl import build_fuzzy_filter, parse_sort, parse_where

app = typer.Typer(help="多维表格「记录」读写与查询", no_args_is_help=True)

MAX_BATCH = 500
DEFAULT_PAGE_SIZE = 100


# -------- helpers --------

def _records_base(app_token: str, table_id: str) -> str:
    return f"/bitable/v1/apps/{app_token}/tables/{table_id}/records"


def _list_page(client: LarkClient, app_token: str, table_id: str, page_size: int, page_token: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token
    return client.get(_records_base(app_token, table_id), params=params).get("data") or {}


def _search_page(
    client: LarkClient,
    app_token: str,
    table_id: str,
    body: dict[str, Any],
    page_size: int,
    page_token: str | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token
    return client.post(
        _records_base(app_token, table_id) + "/search",
        json_body=body,
        params=params,
    ).get("data") or {}


# -------- read commands --------

@app.command("list")
@handle_api_error
def list_records(
    ctx: typer.Context,
    table_id: str = typer.Argument(...),
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a"),
    page_size: int = typer.Option(DEFAULT_PAGE_SIZE, "--page-size"),
    fetch_all: bool = typer.Option(False, "--all", help="自动分页聚合全部记录"),
    limit_show: int = typer.Option(10, "--show", help="JSON 输出中展示前 N 条"),
) -> None:
    """分页列出记录（不带筛选）。"""
    token = resolve_app_token(ctx, app_token)
    with build_client(ctx) as client:
        items = paginate_all(
            lambda pt: _list_page(client, token, table_id, page_size, pt),
            fetch_all,
        )
    emit_json(summarize_records(items, limit=limit_show))


@app.command("get")
@handle_api_error
def get_record(
    ctx: typer.Context,
    table_id: str = typer.Argument(...),
    record_id: str = typer.Argument(...),
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a"),
) -> None:
    """读取单条记录。"""
    token = resolve_app_token(ctx, app_token)
    with build_client(ctx) as client:
        data = client.get(f"{_records_base(token, table_id)}/{record_id}")
    emit_json(data.get("data", {}))


@app.command("search")
@handle_api_error
def search_records(
    ctx: typer.Context,
    table_id: str = typer.Argument(...),
    filter_json: Optional[str] = typer.Option(None, "--filter", help="透传 Feishu 原生 filter JSON"),
    where: Optional[str] = typer.Option(None, "--where", help="简化 DSL: `name contains \"abc\" and status = active`"),
    sort_spec: Optional[str] = typer.Option(None, "--sort", help="如 `created_time desc, name asc`"),
    fields_spec: Optional[str] = typer.Option(None, "--fields", help="只取指定字段，逗号分隔"),
    fuzzy: Optional[str] = typer.Option(None, "--fuzzy", help="在所有文本字段上 OR contains 模糊搜索"),
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a"),
    page_size: int = typer.Option(DEFAULT_PAGE_SIZE, "--page-size"),
    fetch_all: bool = typer.Option(False, "--all"),
    limit_show: int = typer.Option(10, "--show"),
    client_fuzzy: bool = typer.Option(False, "--client-fuzzy", help="在客户端做模糊过滤而非服务端"),
) -> None:
    """核心查询命令：支持原生 filter、简化 DSL、排序、字段选择、模糊搜索、自动分页。"""
    token = resolve_app_token(ctx, app_token)

    # Resolve filter
    filter_obj: dict[str, Any] | None = None
    if filter_json:
        filter_obj = json.loads(filter_json)
    elif where:
        filter_obj = parse_where(where)

    body: dict[str, Any] = {}
    if filter_obj:
        body["filter"] = filter_obj
    if sort_spec:
        body["sort"] = parse_sort(sort_spec)
    if fields_spec:
        body["field_names"] = [s.strip() for s in fields_spec.split(",") if s.strip()]

    # Strategy for --fuzzy:
    #   - No pre-existing filter: fold the fuzzy OR-contains filter into body["filter"]
    #     so the server does the work (cheapest, smallest response).
    #   - Pre-existing filter: Feishu filters are single-level (no nested AND/OR),
    #     so we can't express `(fuzzy OR chain) AND (user filter)` server-side.
    #     Keep the user filter on the server and do the fuzzy pass client-side
    #     over the already-narrowed result set.
    #   - --client-fuzzy: always do the fuzzy pass locally, even without a
    #     pre-existing filter (escape hatch for tables where fuzzy fields
    #     include lookup/formula types).
    apply_client_fuzzy = False
    if fuzzy:
        if client_fuzzy:
            apply_client_fuzzy = True
        elif filter_obj:
            # Keep user filter server-side, fuzzy goes client-side.
            apply_client_fuzzy = True
            emit_warn(
                "[i] --fuzzy 与 --where/--filter 同时指定：服务端仅执行过滤条件，"
                "模糊搜索在客户端对结果集二次过滤。"
            )
        else:
            with build_client(ctx) as client:
                text_field_names = _fetch_text_field_names(client, token, table_id)
                fuzzy_filter = build_fuzzy_filter(fuzzy, text_field_names)
                if fuzzy_filter:
                    body["filter"] = fuzzy_filter
                else:
                    # No text-like fields exist — fall back to client-side.
                    apply_client_fuzzy = True
                items = paginate_all(
                    lambda pt: _search_page(client, token, table_id, body, page_size, pt),
                    fetch_all,
                )
            if apply_client_fuzzy:
                items = _client_fuzzy_filter(items, fuzzy)
            emit_json(summarize_records(items, limit=limit_show))
            return

    with build_client(ctx) as client:
        items = paginate_all(
            lambda pt: _search_page(client, token, table_id, body, page_size, pt),
            fetch_all,
        )
    if fuzzy and apply_client_fuzzy:
        items = _client_fuzzy_filter(items, fuzzy)
    emit_json(summarize_records(items, limit=limit_show))


def _fetch_text_field_names(client: LarkClient, app_token: str, table_id: str) -> list[str]:
    fields = _fetch_all_fields(client, app_token, table_id)
    return [
        str(f.get("field_name", ""))
        for f in fields
        if is_text_like(int(f.get("type", 0))) and f.get("field_name")
    ]


def _client_fuzzy_filter(items: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    # Walk each record's `fields` dict and concat the string-shaped leaves.
    # Avoiding json.dumps on every record keeps this O(N × text_size) rather
    # than O(N × full_record_size).
    q = query.lower()
    out: list[dict[str, Any]] = []
    for r in items:
        if _record_contains(r.get("fields") or {}, q):
            out.append(r)
    return out


def _record_contains(fields: dict[str, Any], needle: str) -> bool:
    for v in fields.values():
        if _value_contains(v, needle):
            return True
    return False


def _value_contains(v: Any, needle: str) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return needle in v.lower()
    if isinstance(v, (int, float, bool)):
        return needle in str(v).lower()
    if isinstance(v, dict):
        return any(_value_contains(x, needle) for x in v.values())
    if isinstance(v, list):
        return any(_value_contains(x, needle) for x in v)
    return False


# -------- write commands --------

@app.command("create")
@handle_api_error
def create_record(
    ctx: typer.Context,
    table_id: str = typer.Argument(...),
    data: Optional[str] = typer.Option(None, "--data", help="fields 的 JSON 对象"),
    file: Optional[str] = typer.Option(None, "--file", help="从 JSON 文件读取 fields"),
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """创建单条记录（写操作，需 --confirm）。"""
    token = resolve_app_token(ctx, app_token)
    fields = load_json_arg(data, file)
    guard_write(
        action="create record",
        target=f"table {table_id}",
        details={"fields_preview": fields},
        confirm=confirm,
    )
    with build_client(ctx) as client:
        resp = client.post(_records_base(token, table_id), json_body={"fields": fields})
    emit_json(resp.get("data", {}))


@app.command("update")
@handle_api_error
def update_record(
    ctx: typer.Context,
    table_id: str = typer.Argument(...),
    record_id: str = typer.Argument(...),
    data: Optional[str] = typer.Option(None, "--data"),
    file: Optional[str] = typer.Option(None, "--file"),
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """更新单条记录（写操作，需 --confirm）。"""
    token = resolve_app_token(ctx, app_token)
    fields = load_json_arg(data, file)
    guard_write(
        action="update record",
        target=f"record {record_id} in table {table_id}",
        details={"fields_preview": fields},
        confirm=confirm,
    )
    with build_client(ctx) as client:
        resp = client.put(
            f"{_records_base(token, table_id)}/{record_id}", json_body={"fields": fields}
        )
    emit_json(resp.get("data", {}))


@app.command("delete")
@handle_api_error
def delete_record(
    ctx: typer.Context,
    table_id: str = typer.Argument(...),
    record_id: str = typer.Argument(...),
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """删除单条记录（写操作，需 --confirm）。"""
    token = resolve_app_token(ctx, app_token)
    guard_write(
        action="delete record",
        target=f"record {record_id} in table {table_id}",
        details={"irreversible": True},
        confirm=confirm,
    )
    with build_client(ctx) as client:
        resp = client.delete(f"{_records_base(token, table_id)}/{record_id}")
    emit_json(resp.get("data", {}))


@app.command("batch-create")
@handle_api_error
def batch_create(
    ctx: typer.Context,
    table_id: str = typer.Argument(...),
    file: str = typer.Option(..., "--file", help="JSON 数组：[{fields:{...}}, ...]"),
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """批量创建记录（自动按 500 条分块）。写操作，需 --confirm。"""
    from pathlib import Path

    token = resolve_app_token(ctx, app_token)
    records = json.loads(Path(file).read_text(encoding="utf-8"))
    if not isinstance(records, list):
        typer.echo("--file 必须是记录数组", err=True)
        raise typer.Exit(code=2)
    guard_write(
        action="batch create records",
        target=f"table {table_id}",
        details={"count": len(records), "chunks": (len(records) + MAX_BATCH - 1) // MAX_BATCH},
        confirm=confirm,
    )
    created: list[dict[str, Any]] = []
    with build_client(ctx) as client:
        for i in range(0, len(records), MAX_BATCH):
            chunk = records[i : i + MAX_BATCH]
            resp = client.post(
                f"{_records_base(token, table_id)}/batch_create",
                json_body={"records": chunk},
            )
            created.extend((resp.get("data") or {}).get("records") or [])
    emit_json({"created": len(created), "records": created})


@app.command("batch-update")
@handle_api_error
def batch_update(
    ctx: typer.Context,
    table_id: str = typer.Argument(...),
    file: str = typer.Option(..., "--file", help="JSON 数组：[{record_id, fields:{...}}, ...]"),
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """批量更新记录（自动按 500 条分块）。写操作，需 --confirm。"""
    from pathlib import Path

    token = resolve_app_token(ctx, app_token)
    records = json.loads(Path(file).read_text(encoding="utf-8"))
    if not isinstance(records, list):
        typer.echo("--file 必须是记录数组", err=True)
        raise typer.Exit(code=2)
    guard_write(
        action="batch update records",
        target=f"table {table_id}",
        details={"count": len(records)},
        confirm=confirm,
    )
    updated: list[dict[str, Any]] = []
    with build_client(ctx) as client:
        for i in range(0, len(records), MAX_BATCH):
            chunk = records[i : i + MAX_BATCH]
            resp = client.post(
                f"{_records_base(token, table_id)}/batch_update",
                json_body={"records": chunk},
            )
            updated.extend((resp.get("data") or {}).get("records") or [])
    emit_json({"updated": len(updated), "records": updated})


@app.command("batch-delete")
@handle_api_error
def batch_delete(
    ctx: typer.Context,
    table_id: str = typer.Argument(...),
    ids: str = typer.Option(..., "--ids", help="record_id 列表，逗号分隔"),
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """批量删除记录（自动按 500 条分块）。写操作，需 --confirm。"""
    token = resolve_app_token(ctx, app_token)
    id_list = [s.strip() for s in ids.split(",") if s.strip()]
    guard_write(
        action="batch delete records",
        target=f"table {table_id}",
        details={"count": len(id_list), "irreversible": True},
        confirm=confirm,
    )
    deleted: list[dict[str, Any]] = []
    with build_client(ctx) as client:
        for i in range(0, len(id_list), MAX_BATCH):
            chunk = id_list[i : i + MAX_BATCH]
            resp = client.post(
                f"{_records_base(token, table_id)}/batch_delete",
                json_body={"records": chunk},
            )
            deleted.extend((resp.get("data") or {}).get("records") or [])
    emit_json({"deleted": len(deleted), "records": deleted})
