"""Shared helpers for command modules."""

from __future__ import annotations

from typing import Any, Callable

import typer

from feishu_super.client import FeishuApiError, LarkClient
from feishu_super.config import MissingCredentialError, ResolvedConfig, resolve_config
from feishu_super.formatters import emit_error, emit_warn

# Hard cap on how many pages any --all pagination will pull. Feishu tables can
# hold millions of records — without a cap a runaway command could hang the CLI
# and blow memory. When this cap is hit we warn the user explicitly so they
# know results were truncated, rather than silently returning partial data.
MAX_PAGES = 50


def build_client(ctx: typer.Context) -> LarkClient:
    cfg: ResolvedConfig = ctx.obj["config"]
    try:
        app_id = cfg.require("FEISHU_APP_ID")
        app_secret = cfg.require("FEISHU_APP_SECRET")
    except MissingCredentialError as e:
        emit_error(str(e))
        raise typer.Exit(code=1) from e
    return LarkClient(app_id=app_id, app_secret=app_secret)


def resolve_app_token(ctx: typer.Context, explicit: str | None) -> str:
    if explicit:
        return explicit
    cfg: ResolvedConfig = ctx.obj["config"]
    tok = cfg.get("FEISHU_APP_TOKEN")
    if not tok:
        emit_error(
            "缺少 app_token。请用 --app-token 指定，或在 .env 中设置 FEISHU_APP_TOKEN。"
        )
        raise typer.Exit(code=1)
    return tok


def handle_api_error(fn):  # type: ignore[no-untyped-def]
    """Decorator: catch FeishuApiError and exit 1 with a clean message."""
    from functools import wraps

    @wraps(fn)
    def wrapped(*args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            return fn(*args, **kwargs)
        except FeishuApiError as e:
            emit_error(f"飞书 API 错误: code={e.code} msg={e.msg}")
            raise typer.Exit(code=1) from e

    return wrapped


def paginate_all(
    fetch_page: Callable[[str | None], dict[str, Any]],
    fetch_all: bool,
    *,
    max_pages: int = MAX_PAGES,
    resource_label: str = "records",
) -> list[dict[str, Any]]:
    """Shared pagination driver for any Feishu list/search endpoint.

    `fetch_page(page_token)` must return the endpoint's `data` dict, i.e. a
    mapping containing `items`, `has_more`, `page_token`. A None/empty response
    is treated as end-of-stream.
    """
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    pages = max_pages if fetch_all else 1
    has_more = False
    for _ in range(pages):
        data = fetch_page(page_token) or {}
        items.extend(data.get("items") or [])
        has_more = bool(data.get("has_more"))
        if not fetch_all or not has_more:
            return items
        page_token = data.get("page_token")
        if not page_token:
            return items
    if has_more:
        emit_warn(
            f"[!] --all 达到 {max_pages} 页上限（约 {len(items)} 条 {resource_label}），"
            f"结果已截断。建议加 --filter/--where 缩小范围，或手动迭代 --page-size/--page-token。"
        )
    return items


def load_json_arg(raw: str | None, file_path: str | None) -> Any:
    """Load JSON either from --data '...' or --file path."""
    import json
    from pathlib import Path

    if raw and file_path:
        emit_error("--data 和 --file 只能选一个")
        raise typer.Exit(code=2)
    if file_path:
        return json.loads(Path(file_path).read_text(encoding="utf-8"))
    if raw:
        return json.loads(raw)
    emit_error("必须提供 --data 或 --file")
    raise typer.Exit(code=2)
