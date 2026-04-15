"""LarkClient: thin wrapper around Feishu Open Platform HTTP endpoints.

- Obtains and caches tenant_access_token (persistent, 5-min early refresh).
- Retries on Feishu rate-limit error codes (99991400, 1254607) with exponential
  backoff.
- On token-invalid codes (99991663, 99991668) clears cache and retries once.
- Every business error surfaces as FeishuApiError(code, msg).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from feishu_super import token_cache

BASE_URL = "https://open.feishu.cn/open-apis"
# 60s leaves headroom for page_size=500 responses (can reach several MB on
# wide tables). POST endpoints (records/search) deliberately do NOT retry on
# transport errors (see request() — POST retry risks double-applying writes),
# so a single timeout is fatal for the entire query. Conservative timeout
# avoids spurious failures under variable network conditions.
DEFAULT_TIMEOUT = 60.0

RATE_LIMIT_CODES: frozenset[int] = frozenset({99991400, 1254607})
TOKEN_INVALID_CODES: frozenset[int] = frozenset({99991663, 99991668})
MAX_RETRIES = 3


class FeishuApiError(RuntimeError):
    def __init__(self, code: int, msg: str, *, url: str | None = None, payload: Any = None):
        self.code = code
        self.msg = msg
        self.url = url
        self.payload = payload
        super().__init__(f"[Feishu {code}] {msg}" + (f" (url={url})" if url else ""))


@dataclass
class LarkClient:
    app_id: str
    app_secret: str
    base_url: str = BASE_URL
    timeout: float = DEFAULT_TIMEOUT
    _http: httpx.Client | None = None
    _token: str | None = None
    # RLock (not Lock) because _invalidate_token is invoked from request()
    # while it already holds the lock via _get_token; an Lock would deadlock.
    # httpx.Client is already thread-safe, so the lock only protects the
    # token state itself, not the HTTP layer.
    _token_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self) -> None:
        self._http = httpx.Client(base_url=self.base_url, timeout=self.timeout)

    # -------- token --------

    def _fetch_token(self) -> str:
        assert self._http is not None
        resp = self._http.post(
            "/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", 0) != 0:
            raise FeishuApiError(
                int(data.get("code", -1)),
                str(data.get("msg", "unknown")),
                url="/auth/v3/tenant_access_token/internal",
                payload=data,
            )
        token = str(data["tenant_access_token"])
        expire = int(data.get("expire", 7200))
        token_cache.save(self.app_id, token, expire)
        self._token = token
        return token

    def _get_token(self, *, force_refresh: bool = False) -> str:
        # The double-checked-locking pattern keeps the fast path lock-free for
        # the >99% case where _token is already set and fresh.
        if not force_refresh and self._token:
            return self._token
        with self._token_lock:
            if not force_refresh and self._token:
                return self._token
            if not force_refresh:
                cached = token_cache.load(self.app_id)
                if cached and cached.is_fresh():
                    self._token = cached.token
                    return cached.token
            return self._fetch_token()

    def _invalidate_token(self) -> None:
        with self._token_lock:
            token_cache.purge(self.app_id)
            self._token = None

    # -------- request --------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> dict[str, Any]:
        assert self._http is not None
        method_upper = method.upper()
        # Only idempotent methods can be safely re-sent on transport error:
        # a retried POST/PUT/DELETE risks double-applying a write if the
        # original request reached the server before we gave up on it.
        retry_on_network_error = method_upper in ("GET", "HEAD")
        token = self._get_token()
        attempt = 0
        while attempt < MAX_RETRIES:
            attempt += 1
            try:
                resp = self._http.request(
                    method_upper,
                    path,
                    params=params,
                    json=json_body,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError:
                if retry_on_network_error and attempt < MAX_RETRIES:
                    time.sleep(1.0 * (2 ** (attempt - 1)))
                    continue
                raise

            try:
                data = resp.json()
            except ValueError:
                raise FeishuApiError(-1, f"非 JSON 响应 (HTTP {resp.status_code}): {resp.text[:200]}", url=path)

            code = int(data.get("code", 0))
            if code == 0:
                return data

            if code in TOKEN_INVALID_CODES and attempt == 1:
                self._invalidate_token()
                token = self._get_token(force_refresh=True)
                continue

            if code in RATE_LIMIT_CODES and attempt < MAX_RETRIES:
                time.sleep(1.0 * (2 ** (attempt - 1)))
                continue

            raise FeishuApiError(code, str(data.get("msg", "unknown")), url=path, payload=data)

        raise FeishuApiError(-1, "重试耗尽但无明确错误", url=path)

    # -------- convenience wrappers --------

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("GET", path, params=params)

    def post(self, path: str, json_body: Any = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("POST", path, json_body=json_body, params=params)

    def put(self, path: str, json_body: Any = None) -> dict[str, Any]:
        return self.request("PUT", path, json_body=json_body)

    def delete(self, path: str) -> dict[str, Any]:
        return self.request("DELETE", path)

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def __enter__(self) -> "LarkClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
