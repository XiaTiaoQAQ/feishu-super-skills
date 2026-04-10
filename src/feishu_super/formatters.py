"""Output helpers: JSON for machine consumption, Rich tables for humans."""

from __future__ import annotations

import json
import sys
from typing import Any, Iterable, Sequence

from rich.console import Console
from rich.table import Table

from feishu_super.field_types import type_name

_stdout_console = Console()
_stderr_console = Console(stderr=True)


def is_tty() -> bool:
    return sys.stdout.isatty()


def emit_json(data: Any) -> None:
    """Always write JSON to stdout (machine-friendly)."""
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    sys.stdout.flush()


def emit_error(msg: str) -> None:
    _stderr_console.print(f"[red]{msg}[/red]")


def emit_warn(msg: str) -> None:
    _stderr_console.print(f"[yellow]{msg}[/yellow]")


def emit_info(msg: str) -> None:
    _stderr_console.print(msg)


def print_table(title: str, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    """Render to stderr so stdout stays clean for JSON."""
    table = Table(title=title, show_lines=False)
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(x) for x in row])
    _stderr_console.print(table)


def format_fields(field_items: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    """Convert Feishu field list → (field_id, field_name, type, property_hint)."""
    out: list[tuple[str, str, str, str]] = []
    for f in field_items:
        code = int(f.get("type", 0))
        prop = f.get("property") or {}
        hint = ""
        if code == 3 or code == 4:
            opts = [o.get("name", "") for o in (prop.get("options") or [])]
            hint = ", ".join(opts[:6]) + ("…" if len(opts) > 6 else "")
        elif code in (18, 21):
            hint = f"→ {prop.get('table_id', '?')}"
        elif code == 19:
            hint = f"lookup→ {prop.get('target_field', '?')}"
        out.append(
            (
                str(f.get("field_id", "")),
                str(f.get("field_name", "")),
                f"{code}:{type_name(code)}",
                hint,
            )
        )
    return out


def summarize_records(records: list[dict[str, Any]], limit: int = 10) -> dict[str, Any]:
    """Return a compact dict summary: total + first N records."""
    return {
        "total": len(records),
        "showing": min(limit, len(records)),
        "records": records[:limit],
    }
