"""Unit tests for the write-operation guard."""

from __future__ import annotations

import pytest
import typer

from feishu_super.guard import guard_write


def test_guard_exits_when_not_confirmed(capsys):
    with pytest.raises(typer.Exit) as excinfo:
        guard_write(
            action="delete record",
            target="table tbl_xxx",
            details={"record_id": "rec_123"},
            confirm=False,
        )
    assert excinfo.value.exit_code == 2
    err = capsys.readouterr().err
    assert "DRY RUN" in err
    assert "[GUARD]" in err
    assert "rec_123" in err


def test_guard_passes_when_confirmed(capsys):
    # Should not raise
    guard_write(
        action="create record",
        target="table tbl_xxx",
        details={"fields_preview": {"name": "test"}},
        confirm=True,
    )
    err = capsys.readouterr().err
    assert "执行中" in err or "create record" in err
    assert "[GUARD]" not in err


def test_guard_preview_renders_nested_details(capsys):
    with pytest.raises(typer.Exit):
        guard_write(
            action="batch create",
            target="table tbl_xxx",
            details={"count": 3, "chunks": 1},
            confirm=False,
        )
    err = capsys.readouterr().err
    assert "batch create" in err
