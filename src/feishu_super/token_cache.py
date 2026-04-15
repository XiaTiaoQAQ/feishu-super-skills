"""Persistent tenant_access_token cache.

Each (app_id) gets its own cache file under ~/.cache/feishu-super/.
Tokens are considered expired 5 minutes before their real expiry, matching the
behavior of dongjing-manager/app/dongjing-manager-backend/src/feishu/config.ts.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

CACHE_DIR = Path(os.environ.get("FEISHU_SUPER_CACHE_DIR", Path.home() / ".cache" / "feishu-super"))
PRE_EXPIRE_SECONDS = 300  # refresh 5 minutes early


@dataclass
class TokenEntry:
    token: str
    expires_at: float  # unix seconds

    def is_fresh(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        return bool(self.token) and (now < self.expires_at - PRE_EXPIRE_SECONDS)


def _cache_path(app_id: str) -> Path:
    digest = hashlib.sha1(app_id.encode("utf-8")).hexdigest()[:12]
    return CACHE_DIR / f"token-{digest}.json"


def load(app_id: str) -> TokenEntry | None:
    p = _cache_path(app_id)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return TokenEntry(token=data["token"], expires_at=float(data["expires_at"]))
    except Exception:
        return None


def save(app_id: str, token: str, expires_in: int) -> TokenEntry:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    entry = TokenEntry(token=token, expires_at=time.time() + float(expires_in))
    payload = {"token": entry.token, "expires_at": entry.expires_at}
    p = _cache_path(app_id)
    # Atomic write: tempfile.mkstemp guarantees a unique path even across
    # threads of the same process, then os.replace renames atomically. This
    # protects against (a) parallel CLI invocations racing on the same file
    # and (b) multiple threads in one process racing on the same write.
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{p.name}.tmp.", dir=str(p.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload))
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        os.replace(tmp_name, p)
    except BaseException:
        # Best-effort cleanup if anything before os.replace blew up.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return entry


def purge(app_id: str) -> None:
    p = _cache_path(app_id)
    if p.is_file():
        try:
            p.unlink()
        except OSError:
            pass
