"""Probe page_size upper bound and concurrent-request behavior.

Two questions:
  1. What's the real max page_size for records/list and records/search?
     CLI defaults to 100, expand uses 500. Feishu docs are vague — try 500,
     1000, 2000, 5000 until we see an error or get clamped.
  2. Can we issue concurrent requests safely? Test 1/2/4/8 workers fetching
     non-overlapping pages and watch for 99991400 / 1254607 rate-limit codes.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from feishu_super.client import LarkClient
from feishu_super.config import resolve_config

APP_TOKEN = "KTLLbJB1gaiZpKsd0KPcaOF9n9f"
SALES_TABLE = "tbl6l9seDw6ai7zm"

# ---------- helpers ----------

def list_path():
    return f"/bitable/v1/apps/{APP_TOKEN}/tables/{SALES_TABLE}/records"

def search_path():
    return f"{list_path()}/search"

def list_one(client, page_size, page_token=None):
    params = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token
    return client.get(list_path(), params=params)

def search_one(client, page_size, page_token=None):
    params = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token
    return client.post(search_path(), json_body={}, params=params)

# ---------- 1. page_size upper bound ----------

def probe_page_size(label, fetch):
    print(f"\n## {label} page_size 上限探测")
    sizes = [100, 500, 1000, 2000, 5000]
    for s in sizes:
        try:
            t0 = time.perf_counter()
            resp = fetch(s)
            dt = time.perf_counter() - t0
            data = resp.get("data") or {}
            items = data.get("items") or []
            has_more = data.get("has_more")
            code = resp.get("code")
            print(f"  page_size={s:<5} -> code={code} returned={len(items)} has_more={has_more} elapsed={dt:.2f}s")
            if len(items) < s and not has_more:
                print(f"    (表只有 {len(items)} 行，无法验证上限是否达到 {s})")
        except Exception as e:
            msg = str(e)[:200]
            print(f"  page_size={s:<5} -> ERROR {msg}")
            # If we got a 1254xxx error the API rejected the size — don't keep climbing
            if "1254" in msg or "InvalidParam" in msg:
                break

# ---------- 2. concurrency probe ----------

def fetch_page(real_client_cfg, body_or_none, page_size, page_token):
    """Each worker gets its own LarkClient so token cache isn't a contention point."""
    c = LarkClient(app_id=real_client_cfg["app_id"], app_secret=real_client_cfg["app_secret"])
    if body_or_none is None:
        return c.get(list_path(), params={"page_size": page_size, "page_token": page_token} if page_token else {"page_size": page_size})
    else:
        params = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        return c.post(search_path(), json_body=body_or_none, params=params)


def collect_page_tokens(seed_client, page_size, body_or_none, max_pages=30):
    """Walk the table sequentially once and collect every page_token, so we
    can later schedule them across workers in parallel.

    IMPORTANT: list and search return DIFFERENT page_token namespaces. Use
    body_or_none=None to collect via /records, body_or_none={} via /search.
    """
    tokens: list[str | None] = [None]  # first page has no token
    pt = None
    for _ in range(max_pages):
        if body_or_none is None:
            data = list_one(seed_client, page_size, pt).get("data") or {}
        else:
            data = search_one(seed_client, page_size, pt).get("data") or {}
        nxt = data.get("page_token")
        if not data.get("has_more") or not nxt:
            break
        tokens.append(nxt)
        pt = nxt
    return tokens


def probe_concurrency(cfg_dict, page_size, body_or_none, label):
    """Issue all pages in parallel with N workers, measure wall time + errors."""
    print(f"\n## {label} 并发探测 (page_size={page_size})")
    # Seed: walk the table once sequentially to collect page_tokens
    seed = LarkClient(app_id=cfg_dict["app_id"], app_secret=cfg_dict["app_secret"])
    t0 = time.perf_counter()
    tokens = collect_page_tokens(seed, page_size, body_or_none)
    seed_dt = time.perf_counter() - t0
    print(f"  收集 page_tokens: {len(tokens)} 页, 顺序耗时 {seed_dt:.2f}s")

    for workers in [1, 2, 4, 8]:
        t0 = time.perf_counter()
        ok = 0
        err = 0
        rate_limited = False
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(fetch_page, cfg_dict, body_or_none, page_size, tok) for tok in tokens]
            for f in as_completed(futs):
                try:
                    resp = f.result()
                    code = resp.get("code")
                    if code == 0:
                        ok += 1
                    else:
                        err += 1
                        if code in (99991400, 1254607):
                            rate_limited = True
                        print(f"    page error code={code} msg={resp.get('msg')}")
                except Exception as e:
                    err += 1
                    msg = str(e)[:200]
                    print(f"    page exception {msg}")
                    if "1254607" in msg or "99991400" in msg or "限速" in msg:
                        rate_limited = True
        dt = time.perf_counter() - t0
        print(f"  workers={workers:<2} -> wall={dt:.2f}s ok={ok} err={err} rate_limited={rate_limited}")


def main():
    cfg = resolve_config()
    cfg_dict = {"app_id": cfg.require("FEISHU_APP_ID"), "app_secret": cfg.require("FEISHU_APP_SECRET")}
    seed = LarkClient(**cfg_dict)

    # ---- Part 2 only (page-size already known) ----
    probe_concurrency(cfg_dict, 500, {}, "records/search")


if __name__ == "__main__":
    main()
