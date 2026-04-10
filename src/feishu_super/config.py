"""Environment resolution with multi-source fallback and provenance tracking.

Priority (highest → lowest):
  1. Explicit CLI args (handled by cli layer, not here)
  2. Shell environment variables
  3. $PWD/.env
  4. $PWD/.env.local
  5. Skill directory .env (the directory containing this package)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

REQUIRED_KEYS = ("FEISHU_APP_ID", "FEISHU_APP_SECRET")
OPTIONAL_KEYS = ("FEISHU_APP_TOKEN",)
ALL_KEYS = REQUIRED_KEYS + OPTIONAL_KEYS

# The skill package lives at src/feishu_super/config.py, so the skill root is
# three parents up from this file.
SKILL_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class ResolvedConfig:
    values: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)  # key -> source label

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def require(self, key: str) -> str:
        v = self.values.get(key)
        if not v:
            raise MissingCredentialError(
                f"缺少必需的环境变量 {key}。请在 .env 中配置，或用 `feishu-super env` 查看当前来源。"
            )
        return v

    def missing(self, keys: tuple[str, ...] = REQUIRED_KEYS) -> list[str]:
        return [k for k in keys if not self.values.get(k)]


class MissingCredentialError(RuntimeError):
    pass


def _load_env_file(path: Path, label: str, out: ResolvedConfig) -> None:
    if not path.is_file():
        return
    # Skip file I/O entirely if every key we care about is already resolved
    # from a higher-priority source.
    if all(out.values.get(k) for k in ALL_KEYS):
        return
    try:
        data = dotenv_values(path)
    except OSError:
        # Unreadable file — not fatal, just skip. Permissions etc.
        return
    for key in ALL_KEYS:
        val = data.get(key)
        if val and not out.values.get(key):
            out.values[key] = val
            out.sources[key] = f"{label} ({path})"


def resolve_config(
    cwd: Path | None = None,
    overrides: dict[str, str | None] | None = None,
) -> ResolvedConfig:
    """Resolve FEISHU_* config by walking the priority chain."""
    cwd = cwd or Path.cwd()
    out = ResolvedConfig()

    # 1. CLI overrides (highest priority)
    if overrides:
        for key, val in overrides.items():
            if val:
                out.values[key] = val
                out.sources[key] = "cli arg"

    # 2. Shell environment
    for key in ALL_KEYS:
        if not out.values.get(key):
            val = os.environ.get(key)
            if val:
                out.values[key] = val
                out.sources[key] = "shell env"

    # 3. $PWD/.env
    _load_env_file(cwd / ".env", "cwd .env", out)

    # 4. $PWD/.env.local
    _load_env_file(cwd / ".env.local", "cwd .env.local", out)

    # 5. Skill directory .env
    _load_env_file(SKILL_ROOT / ".env", "skill .env", out)

    return out


def describe_config(cfg: ResolvedConfig) -> list[tuple[str, str, str]]:
    """Return [(key, status, source)] for human display. Secrets are masked."""
    rows: list[tuple[str, str, str]] = []
    for key in ALL_KEYS:
        val = cfg.values.get(key)
        if val:
            masked = _mask(val)
            rows.append((key, masked, cfg.sources.get(key, "?")))
        else:
            required = key in REQUIRED_KEYS
            rows.append((key, "<未设置>" + (" (必需)" if required else " (可选)"), "-"))
    return rows


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"
