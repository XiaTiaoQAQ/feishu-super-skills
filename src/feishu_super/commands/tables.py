"""Table-level commands: list / get / create / delete."""

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
from feishu_super.formatters import emit_json, print_table
from feishu_super.guard import guard_write

app = typer.Typer(help="多维表格「数据表」管理", no_args_is_help=True)


def _fetch_all_tables(client, app_token: str):
    path = f"/bitable/v1/apps/{app_token}/tables"

    def fetch(pt: str | None):
        params: dict = {"page_size": 100}
        if pt:
            params["page_token"] = pt
        return client.get(path, params=params).get("data") or {}

    return paginate_all(fetch, fetch_all=True, resource_label="tables")


@app.command("list")
@handle_api_error
def list_tables(
    ctx: typer.Context,
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a", help="多维表格 App Token"),
    name_like: Optional[str] = typer.Option(None, "--name", help="按表名做子串过滤"),
) -> None:
    """列出指定 App 下的所有数据表。"""
    token = resolve_app_token(ctx, app_token)
    with build_client(ctx) as client:
        items = _fetch_all_tables(client, token)
    if name_like:
        items = [t for t in items if name_like in str(t.get("name", ""))]
    print_table(
        f"App {token} 下共 {len(items)} 个数据表",
        ["table_id", "name", "revision"],
        [(t.get("table_id", ""), t.get("name", ""), t.get("revision", "")) for t in items],
    )
    emit_json({"app_token": token, "total": len(items), "items": items})


@app.command("get")
@handle_api_error
def get_table(
    ctx: typer.Context,
    table_id: str = typer.Argument(..., help="数据表 ID（tbl 开头）"),
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a"),
) -> None:
    """根据 table_id 查找单个表的元信息。"""
    token = resolve_app_token(ctx, app_token)
    with build_client(ctx) as client:
        items = _fetch_all_tables(client, token)
    found = next((t for t in items if t.get("table_id") == table_id), None)
    if not found:
        typer.echo(f"未找到 table_id={table_id}", err=True)
        raise typer.Exit(code=1)
    emit_json(found)


@app.command("create")
@handle_api_error
def create_table(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name", help="新数据表名称"),
    default_view_name: Optional[str] = typer.Option(None, "--view-name"),
    fields_file: Optional[str] = typer.Option(None, "--fields-file", help="字段定义 JSON 文件"),
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a"),
    confirm: bool = typer.Option(False, "--confirm", help="写操作必须显式确认"),
) -> None:
    """创建数据表（写操作，需 --confirm）。"""
    token = resolve_app_token(ctx, app_token)

    fields_payload: list[dict] = []
    if fields_file:
        from pathlib import Path

        fields_payload = json.loads(Path(fields_file).read_text(encoding="utf-8"))

    body: dict = {"table": {"name": name}}
    if default_view_name:
        body["table"]["default_view_name"] = default_view_name
    if fields_payload:
        body["table"]["fields"] = fields_payload

    guard_write(
        action="create table",
        target=f"app {token}",
        details={"name": name, "fields_count": len(fields_payload)},
        confirm=confirm,
    )

    with build_client(ctx) as client:
        data = client.post(f"/bitable/v1/apps/{token}/tables", json_body=body)
    emit_json(data.get("data", {}))


@app.command("delete")
@handle_api_error
def delete_table(
    ctx: typer.Context,
    table_id: str = typer.Argument(...),
    app_token: Optional[str] = typer.Option(None, "--app-token", "-a"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """删除数据表（写操作，需 --confirm）。"""
    token = resolve_app_token(ctx, app_token)
    guard_write(
        action="delete table",
        target=f"table {table_id} in app {token}",
        details={"irreversible": True},
        confirm=confirm,
    )
    with build_client(ctx) as client:
        data = client.delete(f"/bitable/v1/apps/{token}/tables/{table_id}")
    emit_json(data.get("data", {}))
