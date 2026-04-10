"""Unit tests for config resolution (multi-source fallback)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from feishu_super.config import describe_config, resolve_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_APP_TOKEN"):
        monkeypatch.delenv(key, raising=False)


def test_resolve_from_overrides(tmp_path: Path):
    cfg = resolve_config(
        cwd=tmp_path,
        overrides={"FEISHU_APP_ID": "cli_override", "FEISHU_APP_SECRET": "s"},
    )
    assert cfg.get("FEISHU_APP_ID") == "cli_override"
    assert cfg.sources["FEISHU_APP_ID"] == "cli arg"


def test_resolve_from_shell_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_shell")
    monkeypatch.setenv("FEISHU_APP_SECRET", "ssss")
    cfg = resolve_config(cwd=tmp_path)
    assert cfg.get("FEISHU_APP_ID") == "cli_shell"
    assert cfg.sources["FEISHU_APP_ID"] == "shell env"


def test_resolve_from_cwd_env_file(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "FEISHU_APP_ID=cli_fromfile\nFEISHU_APP_SECRET=filesec\n",
        encoding="utf-8",
    )
    cfg = resolve_config(cwd=tmp_path)
    assert cfg.get("FEISHU_APP_ID") == "cli_fromfile"
    assert "cwd .env" in cfg.sources["FEISHU_APP_ID"]


def test_override_beats_env_file(tmp_path: Path):
    (tmp_path / ".env").write_text("FEISHU_APP_ID=cli_fromfile\n", encoding="utf-8")
    cfg = resolve_config(
        cwd=tmp_path,
        overrides={"FEISHU_APP_ID": "cli_override"},
    )
    assert cfg.get("FEISHU_APP_ID") == "cli_override"


def test_missing_required(tmp_path: Path):
    cfg = resolve_config(cwd=tmp_path)
    assert set(cfg.missing()) == {"FEISHU_APP_ID", "FEISHU_APP_SECRET"}


def test_describe_masks_secrets(tmp_path: Path):
    cfg = resolve_config(
        cwd=tmp_path,
        overrides={
            "FEISHU_APP_ID": "cli_a12345678",
            "FEISHU_APP_SECRET": "mysecrettoken0000",
        },
    )
    rows = describe_config(cfg)
    values = {k: v for k, v, _ in rows}
    assert values["FEISHU_APP_ID"] == "cli_...5678"
    assert values["FEISHU_APP_SECRET"] == "myse...0000"
    assert "<未设置>" in values["FEISHU_APP_TOKEN"]
