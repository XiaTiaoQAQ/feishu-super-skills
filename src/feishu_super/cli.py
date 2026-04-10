"""Top-level Typer app for feishu-super."""

from __future__ import annotations

from typing import Optional

import typer

from feishu_super import __version__
from feishu_super.commands import fields as fields_cmd
from feishu_super.commands import records as records_cmd
from feishu_super.commands import tables as tables_cmd
from feishu_super.config import describe_config, resolve_config
from feishu_super.formatters import print_table

app = typer.Typer(
    help="飞书多维表格高阶 OpenAPI 工具 — 带写操作权限围栏与丰富查询能力",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    app_id: Optional[str] = typer.Option(None, "--app-id", help="覆盖 FEISHU_APP_ID"),
    app_secret: Optional[str] = typer.Option(None, "--app-secret", help="覆盖 FEISHU_APP_SECRET"),
    app_token: Optional[str] = typer.Option(None, "--app-token-default", help="覆盖默认 FEISHU_APP_TOKEN"),
    version: bool = typer.Option(False, "--version", help="打印版本并退出"),
) -> None:
    if version:
        typer.echo(f"feishu-super {__version__}")
        raise typer.Exit()
    cfg = resolve_config(
        overrides={
            "FEISHU_APP_ID": app_id,
            "FEISHU_APP_SECRET": app_secret,
            "FEISHU_APP_TOKEN": app_token,
        }
    )
    ctx.obj = {"config": cfg}


@app.command("env")
def env_cmd(ctx: typer.Context) -> None:
    """诊断当前环境变量来源（不会真正调用飞书 API）。"""
    cfg = ctx.obj["config"]
    rows = describe_config(cfg)
    print_table("feishu-super 环境诊断", ["变量", "值", "来源"], rows)
    missing = cfg.missing()
    if missing:
        typer.echo(f"\n[!] 缺少必需变量: {', '.join(missing)}", err=True)
        raise typer.Exit(code=1)


app.add_typer(tables_cmd.app, name="tables")
app.add_typer(fields_cmd.app, name="fields")
app.add_typer(records_cmd.app, name="records")


if __name__ == "__main__":
    app()
