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
#
# 200 pages × 500 rows/page = 100k row default ceiling. The previous value (50)
# combined with the previous default page_size (100) capped --all at 5000 rows,
# which silently truncated commonplace tables (e.g. 9158-row 销课记录 lost
# 4158 rows without warning when fetched via plain list --all).
MAX_PAGES = 200


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
    items_cap: int | None = None,
) -> list[dict[str, Any]]:
    """Shared pagination driver for any Feishu list/search endpoint.

    `fetch_page(page_token)` must return the endpoint's `data` dict, i.e. a
    mapping containing `items`, `has_more`, `page_token`. A None/empty response
    is treated as end-of-stream.

    `items_cap` (optional): soft ceiling on accumulated items. When the
    accumulator reaches this number, pagination stops early with a warning.
    Useful as a second layer of defense against runaway client_fuzzy-style
    flows that would load 100k records into memory and then scan them.
    """
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    pages = max_pages if fetch_all else 1
    has_more = False
    cap_hit = False
    for _ in range(pages):
        data = fetch_page(page_token) or {}
        items.extend(data.get("items") or [])
        has_more = bool(data.get("has_more"))
        if items_cap is not None and len(items) >= items_cap:
            cap_hit = True
            break
        if not fetch_all or not has_more:
            return items
        page_token = data.get("page_token")
        if not page_token:
            return items
    if cap_hit:
        emit_warn(
            f"[!] --all 达到 items_cap={items_cap}（实际 {len(items)} 条 {resource_label}），"
            f"已停止分页。请用 --filter/--where 收窄查询范围。"
        )
    elif has_more:
        emit_warn(
            f"[!] --all 达到 {max_pages} 页上限（约 {len(items)} 条 {resource_label}），"
            f"结果已截断。建议加 --filter/--where 缩小范围，或手动迭代 --page-size/--page-token。"
        )
    return items


def chunked_post(
    client: LarkClient,
    path: str,
    items: list[Any],
    *,
    body_key: str,
    response_key: str,
    chunk_size: int,
) -> list[dict[str, Any]]:
    """POST `items` in chunks of `chunk_size`, collect response[data][response_key].

    Feishu bulk endpoints (records/batch_create, batch_update, batch_delete,
    records/batch_get) all share the same wire shape: a POST body with one
    array field, a response carrying a parallel array field. They also share
    the same per-request array cap (500 for batch_{create,update,delete},
    100 for batch_get). This helper captures the common loop so callers only
    vary `body_key` / `response_key` / `chunk_size`.

    Concurrency is deliberately NOT handled here — callers that want parallel
    chunks (e.g. expand's batch_get path) wrap this at the call site so each
    API's concurrency policy stays near its API-specific constants.
    """
    out: list[dict[str, Any]] = []
    for i in range(0, len(items), chunk_size):
        chunk = items[i : i + chunk_size]
        resp = client.post(path, json_body={body_key: chunk})
        out.extend(((resp.get("data") or {}).get(response_key)) or [])
    return out


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
