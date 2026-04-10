"""Field-level commands: list / add / delete."""

from __future__ import annotations

import json
from typing import Optional

import typer

from feishu_super.commands._common import (
    build_client,
    handle_api_error,
    paginate_all,
    resolve_app_token,
)
from feishu_super.field_types import is_read_only
from feishu_super.formatters import emit_json, format_fields, print_table
from feishu_super.guard import guard_write

app = typer.Typer(help="多维表格「字段」管理", no_args_is_help=True)


def _fetch_all_fields(client, app_token: str, table_id: str):
    path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields"

    def fetch(pt: str | None):
        params: dict = {"page_size": 100}
        if pt:
            params["page_token"] = pt
        return client.get(path, params=params).get("data") or {}

    return paginate_all(fetch, fetch_all=True, resource_label="fields")


@app.command("list")
@handle_api_error
def list_fields(
    ctx: typer.Context,
    table_id: str = typer.Argument(...),
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a"),
    name_like: Optional[str] = typer.Option(None, "--name"),
    type_filter: Optional[int] = typer.Option(None, "--type", help="按字段类型码过滤"),
) -> None:
    """列出表的全部字段，输出类型码与人类可读名称。"""
    token = resolve_app_token(ctx, app_token)
    with build_client(ctx) as client:
        items = _fetch_all_fields(client, token, table_id)
    if name_like:
        items = [f for f in items if name_like in str(f.get("field_name", ""))]
    if type_filter is not None:
        items = [f for f in items if int(f.get("type", 0)) == type_filter]
    rows = format_fields(items)
    print_table(
        f"表 {table_id} 共 {len(items)} 个字段",
        ["field_id", "field_name", "type", "property"],
        rows,
    )
    emit_json({"table_id": table_id, "total": len(items), "items": items})


@app.command("add")
@handle_api_error
def add_field(
    ctx: typer.Context,
    table_id: str = typer.Argument(...),
    name: str = typer.Option(..., "--name"),
    field_type: int = typer.Option(..., "--type", help="字段类型码，如 1=Text, 2=Number"),
    property_json: Optional[str] = typer.Option(None, "--property", help="字段 property JSON"),
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """添加字段（写操作，需 --confirm）。"""
    token = resolve_app_token(ctx, app_token)
    if is_read_only(field_type):
        typer.echo(f"类型码 {field_type} 为只读字段，不支持通过此命令创建。", err=True)
        raise typer.Exit(code=2)
    body: dict = {"field_name": name, "type": field_type}
    if property_json:
        body["property"] = json.loads(property_json)
    guard_write(
        action="add field",
        target=f"table {table_id}",
        details={"field_name": name, "type": field_type, "property": body.get("property")},
        confirm=confirm,
    )
    with build_client(ctx) as client:
        data = client.post(
            f"/bitable/v1/apps/{token}/tables/{table_id}/fields", json_body=body
        )
    emit_json(data.get("data", {}))


@app.command("delete")
@handle_api_error
def delete_field(
    ctx: typer.Context,
    table_id: str = typer.Argument(...),
    field_id: str = typer.Argument(...),
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """删除字段（写操作，需 --confirm）。"""
    token = resolve_app_token(ctx, app_token)
    guard_write(
        action="delete field",
        target=f"field {field_id} in table {table_id}",
        details={"irreversible": True, "data_loss": "该列所有记录数据会被清除"},
        confirm=confirm,
    )
    with build_client(ctx) as client:
        data = client.delete(
            f"/bitable/v1/apps/{token}/tables/{table_id}/fields/{field_id}"
        )
    emit_json(data.get("data", {}))
