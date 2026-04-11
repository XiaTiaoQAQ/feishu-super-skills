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
from feishu_super.date_range import (
    DateRangeError,
    build_date_range,
    filter_records_by_date,
)
from feishu_super.expand import expand_links
from feishu_super.field_types import is_text_like
from feishu_super.formatters import emit_error, emit_json, emit_warn, summarize_records
from feishu_super.guard import guard_write
from feishu_super.schema import TableSchema, get_table_schema
from feishu_super.where_dsl import DslError, build_fuzzy_filter, parse_sort, parse_where

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


def _maybe_expand(
    client: LarkClient,
    app_token: str,
    table_id: str,
    records: list[dict[str, Any]],
    no_expand: bool,
) -> list[dict[str, Any]]:
    """Apply Link expansion unless the user explicitly opted out."""
    if no_expand or not records:
        return records
    schema = get_table_schema(client, app_token, table_id)
    return expand_links(client, app_token, records, schema)


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
    no_expand: bool = typer.Option(False, "--no-expand", help="关闭 Link 字段自动补齐（records/list 本身已完整，此处为对称支持）"),
) -> None:
    """分页列出记录（不带筛选）。"""
    token = resolve_app_token(ctx, app_token)
    with build_client(ctx) as client:
        items = paginate_all(
            lambda pt: _list_page(client, token, table_id, page_size, pt),
            fetch_all,
        )
        items = _maybe_expand(client, token, table_id, items, no_expand)
    emit_json(summarize_records(items, limit=limit_show))


@app.command("get")
@handle_api_error
def get_record(
    ctx: typer.Context,
    table_id: str = typer.Argument(...),
    record_id: str = typer.Argument(...),
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a"),
    no_expand: bool = typer.Option(False, "--no-expand"),
) -> None:
    """读取单条记录。"""
    token = resolve_app_token(ctx, app_token)
    with build_client(ctx) as client:
        data = client.get(f"{_records_base(token, table_id)}/{record_id}")
        payload = data.get("data") or {}
        rec = payload.get("record")
        if rec:
            _maybe_expand(client, token, table_id, [rec], no_expand)
    emit_json(payload)


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
    no_expand: bool = typer.Option(False, "--no-expand", help="关闭 Link 字段自动补齐"),
    date_field: Optional[str] = typer.Option(None, "--date-field", help="用于日期语义筛选的 DateTime 字段名"),
    date_on: Optional[str] = typer.Option(None, "--date-on", help="筛选某一天 (YYYY-MM-DD)"),
    date_range_spec: Optional[str] = typer.Option(None, "--date-range", help="筛选区间 START..END (YYYY-MM-DD 或毫秒)"),
    date_today: bool = typer.Option(False, "--date-today", help="筛选今天（按 --tz）"),
    date_tomorrow: bool = typer.Option(False, "--date-tomorrow", help="筛选明天（按 --tz）"),
    date_yesterday: bool = typer.Option(False, "--date-yesterday", help="筛选昨天（按 --tz）"),
    tz: Optional[str] = typer.Option(None, "--tz", help="时区名（IANA），默认 Asia/Shanghai"),
) -> None:
    """核心查询命令：支持原生 filter、简化 DSL、排序、字段选择、模糊搜索、日期语义、自动分页。

    关于日期筛选：飞书 records/search 对 DateTime 字段的范围 filter
    (isGreater/isLess/...) 会返回 code=1254018 InvalidFilter。请使用
    --date-field + --date-on/--date-range/--date-today/--date-tomorrow
    这组参数，本命令会自动在客户端做区间过滤。
    """
    token = resolve_app_token(ctx, app_token)

    # Parse date-semantic params upfront so we fail fast on bad input.
    try:
        date_range = build_date_range(
            tz_name=tz,
            on=date_on,
            range_spec=date_range_spec,
            today=date_today,
            tomorrow=date_tomorrow,
            yesterday=date_yesterday,
        )
    except DateRangeError as e:
        emit_error(f"日期参数错误: {e}")
        raise typer.Exit(code=2)

    if date_range and not date_field:
        emit_error("指定日期参数时必须同时提供 --date-field <字段名>")
        raise typer.Exit(code=2)

    # Pre-pull schema if we need DSL type-checking or date-field validation.
    # Schema is cached per (client, table) so repeated access is free.
    schema: TableSchema | None = None
    if where or date_range:
        with build_client(ctx) as probe:
            schema = get_table_schema(probe, token, table_id)
        if date_range and date_field:
            ftype = schema.field_type(date_field)
            if ftype is None:
                emit_error(f"--date-field {date_field!r} 在表 {table_id} 中不存在")
                raise typer.Exit(code=2)
            if ftype != 5:
                emit_error(
                    f"--date-field {date_field!r} 的类型是 {ftype}，不是 DateTime (5)。"
                    "日期语义参数仅支持 DateTime 字段。"
                )
                raise typer.Exit(code=2)

    field_types_for_dsl = {n: m.type for n, m in schema.by_name.items()} if schema else None

    # Resolve filter
    filter_obj: dict[str, Any] | None = None
    if filter_json:
        filter_obj = json.loads(filter_json)
    elif where:
        try:
            filter_obj = parse_where(where, field_types=field_types_for_dsl)
        except DslError as e:
            emit_error(f"--where DSL 错误: {e}")
            raise typer.Exit(code=2)

    body: dict[str, Any] = {}
    if filter_obj:
        body["filter"] = filter_obj
    if sort_spec:
        body["sort"] = parse_sort(sort_spec)
    if fields_spec:
        body["field_names"] = [s.strip() for s in fields_spec.split(",") if s.strip()]

    # Date range is a post-filter: search runs normally, then we drop records
    # whose date_field falls outside the range. This requires exhaustive
    # pagination — otherwise the first 100 search hits might all fall outside
    # the range and we'd return nothing. Auto-force fetch_all.
    if date_range and not fetch_all:
        emit_warn(
            f"[i] 日期筛选 ({date_range.label}) 需要遍历全部记录；已自动开启 --all"
        )
        fetch_all = True
    # Date-filter scans can legitimately need to walk tens of thousands of
    # rows. The default paginate_all cap (50 pages × 100 rows = 5 000) is too
    # low for that case, so raise it when the user is intentionally asking
    # for exhaustive date scanning.
    paginate_max_pages = 1000 if date_range else None

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
            apply_client_fuzzy = True
            emit_warn(
                "[i] --fuzzy 与 --where/--filter 同时指定：服务端仅执行过滤条件，"
                "模糊搜索在客户端对结果集二次过滤。"
            )

    with build_client(ctx) as client:
        # If fuzzy is the ONLY filter, fold it into body server-side.
        if fuzzy and not client_fuzzy and not filter_obj:
            text_field_names = _fetch_text_field_names(client, token, table_id)
            fuzzy_filter = build_fuzzy_filter(fuzzy, text_field_names)
            if fuzzy_filter:
                body["filter"] = fuzzy_filter
            else:
                apply_client_fuzzy = True

        paginate_kwargs: dict[str, Any] = {}
        if paginate_max_pages is not None:
            paginate_kwargs["max_pages"] = paginate_max_pages
        items = paginate_all(
            lambda pt: _search_page(client, token, table_id, body, page_size, pt),
            fetch_all,
            **paginate_kwargs,
        )
        if fuzzy and apply_client_fuzzy:
            items = _client_fuzzy_filter(items, fuzzy)
        if date_range and date_field:
            before = len(items)
            items = filter_records_by_date(items, date_field, date_range)
            emit_warn(
                f"[i] 日期筛选保留 {len(items)}/{before} 条记录"
            )
        items = _maybe_expand(client, token, table_id, items, no_expand)

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
