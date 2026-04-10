"""Permission guard for destructive (write/delete) operations.

Contract:
    guard_write(action, target, details, confirm)

- `confirm=False` → print a preview banner to stderr and raise SystemExit(2).
- `confirm=True`  → print a short summary to stderr (audit trail) and return.

The LLM is expected (via SKILL.md instructions) to invoke destructive commands
WITHOUT --confirm first, show the preview to the user, obtain explicit approval
in natural language ("执行"/"确认"/"yes"), and only then re-invoke with
--confirm. Exit code 2 is a *designed signal*, not an error.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

_stderr_console = Console(stderr=True)


def guard_write(
    action: str,
    target: str,
    details: dict[str, Any] | None = None,
    *,
    confirm: bool,
) -> None:
    """Gate a destructive operation.

    Args:
        action: Short verb phrase, e.g. "create record", "delete table".
        target: What is being touched, e.g. "table tbl_xxx in app KTL...".
        details: Arbitrary dict shown as key/value rows in preview.
        confirm: Whether --confirm was passed on the CLI.
    """
    if confirm:
        _emit_confirmed_summary(action, target, details)
        return

    _emit_preview(action, target, details)
    _stderr_console.print(
        "[bold red][GUARD][/bold red] 此操作未加 [cyan]--confirm[/cyan]，已拒绝执行。\n"
        "请先向用户展示上方预览，获得用户明确确认（如\"确认\"、\"执行\"、\"yes\"）"
        "后再加上 [cyan]--confirm[/cyan] 重试。"
    )
    raise typer.Exit(code=2)


def _emit_preview(action: str, target: str, details: dict[str, Any] | None) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    table.add_row("动作", action)
    table.add_row("目标", target)
    if details:
        for k, v in details.items():
            rendered = _render_value(v)
            table.add_row(str(k), rendered)
    panel = Panel(
        table,
        title="[bold yellow]写操作预览 (DRY RUN)[/bold yellow]",
        border_style="yellow",
    )
    _stderr_console.print(panel)


def _emit_confirmed_summary(action: str, target: str, details: dict[str, Any] | None) -> None:
    bits = [f"[green]✓ 执行中[/green] {action} → {target}"]
    if details:
        # keep it single-line for audit trail
        compact = ", ".join(f"{k}={_render_value(v, max_len=40)}" for k, v in details.items())
        bits.append(f"  {compact}")
    _stderr_console.print("\n".join(bits))


def _render_value(v: Any, max_len: int = 200) -> str:
    import json

    if isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False)
    else:
        s = str(v)
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s
