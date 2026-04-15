"""Performance probe for the low-frequency-customer report path.

Measures, in real seconds and HTTP-call counts, the cost of:
  A. records/list of 销课记录 (full table) — baseline cost
  B. records/search, no filter, no expand — just raw pagination
  C. records/search + date_range (90d) + no_expand — date scan only
  D. records/search + date_range (90d) + expand — full path the agent used
  E. records/search + date_range (30d) + expand — confirm second-pass cost
  F. expand cost broken down: each target-table records/list

All wall-clock + bytes printed to stdout. Script is read-only.
"""
from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager

from feishu_super.client import LarkClient
from feishu_super.config import resolve_config
from feishu_super.commands._common import paginate_all
from feishu_super.date_range import build_date_range, filter_records_by_date
from feishu_super.expand import expand_links
from feishu_super.schema import get_table_schema

APP_TOKEN = "KTLLbJB1gaiZpKsd0KPcaOF9n9f"
SALES_TABLE = "tbl6l9seDw6ai7zm"  # 销课记录（自动）
CUSTOMER_TABLE = "tblDl4dATw3KB8hP"  # 客户明细
DATE_FIELD = "日期"


class CountingClient:
    """Wrap LarkClient and count HTTP calls + payload bytes."""

    def __init__(self, real: LarkClient):
        self._real = real
        self.calls = 0
        self.bytes = 0
        # Per-path call counts so we can attribute cost to specific endpoints.
        self.by_path: dict[str, int] = {}

    def _track(self, path: str, resp: dict) -> dict:
        self.calls += 1
        self.by_path[path] = self.by_path.get(path, 0) + 1
        self.bytes += len(json.dumps(resp, ensure_ascii=False))
        return resp

    def get(self, path, params=None):
        return self._track(path, self._real.get(path, params=params))

    def post(self, path, json_body=None, params=None):
        return self._track(path, self._real.post(path, json_body=json_body, params=params))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


@contextmanager
def stage(name: str):
    print(f"\n=== {name} ===", flush=True)
    t0 = time.perf_counter()
    yield
    dt = time.perf_counter() - t0
    print(f"[{name}] elapsed={dt:.2f}s", flush=True)


def _list_page(c, table_id, page_size, page_token):
    params = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token
    return c.get(f"/bitable/v1/apps/{APP_TOKEN}/tables/{table_id}/records", params=params).get("data") or {}


def _search_page(c, table_id, body, page_size, page_token):
    params = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token
    return c.post(
        f"/bitable/v1/apps/{APP_TOKEN}/tables/{table_id}/records/search",
        json_body=body,
        params=params,
    ).get("data") or {}


def report(label, c: CountingClient, t0):
    dt = time.perf_counter() - t0
    print(f"  -> {label}: {dt:.2f}s, calls={c.calls}, bytes={c.bytes/1024:.1f}KB")
    if c.by_path:
        for p, n in sorted(c.by_path.items(), key=lambda x: -x[1]):
            print(f"     {n:>4}× {p}")


def main():
    cfg = resolve_config()
    real = LarkClient(app_id=cfg.require("FEISHU_APP_ID"), app_secret=cfg.require("FEISHU_APP_SECRET"))

    # --- A: list of full 销课记录, page_size 500 (best case), no expand ---
    with stage("A. records/list 销课记录 全表 page_size=500 no-expand"):
        c = CountingClient(real)
        t0 = time.perf_counter()
        items_a = paginate_all(
            lambda pt: _list_page(c, SALES_TABLE, 500, pt),
            fetch_all=True,
            max_pages=1000,
        )
        print(f"  total records = {len(items_a)}")
        report("A", c, t0)

    # --- A2: same but page_size 100 (CLI default) to see overhead ---
    with stage("A2. records/list 销课记录 全表 page_size=100 (CLI default)"):
        c = CountingClient(real)
        t0 = time.perf_counter()
        items_a2 = paginate_all(
            lambda pt: _list_page(c, SALES_TABLE, 100, pt),
            fetch_all=True,
            max_pages=1000,
        )
        print(f"  total records = {len(items_a2)}")
        report("A2", c, t0)

    # --- B: records/search no filter no expand page_size 500 ---
    with stage("B. records/search 全表 page_size=500 no-expand"):
        c = CountingClient(real)
        t0 = time.perf_counter()
        items_b = paginate_all(
            lambda pt: _search_page(c, SALES_TABLE, {}, 500, pt),
            fetch_all=True,
            max_pages=1000,
        )
        print(f"  total records = {len(items_b)}")
        report("B", c, t0)

    # --- C: records/search + 90d date filter, no expand, page_size 500 ---
    dr_90 = build_date_range(tz_name="Asia/Shanghai", range_spec="2026-01-16..2026-04-15")
    with stage("C. records/search 90d date-filter no-expand"):
        c = CountingClient(real)
        t0 = time.perf_counter()
        all_items = paginate_all(
            lambda pt: _search_page(c, SALES_TABLE, {}, 500, pt),
            fetch_all=True,
            max_pages=1000,
        )
        items_c = filter_records_by_date(all_items, DATE_FIELD, dr_90)
        print(f"  scanned={len(all_items)}, kept(90d)={len(items_c)}")
        report("C", c, t0)

    # --- D: same as C but WITH expand_links (the path the agent actually used) ---
    with stage("D. records/search 90d date-filter + expand_links (full agent path)"):
        c = CountingClient(real)
        t0 = time.perf_counter()
        all_items = paginate_all(
            lambda pt: _search_page(c, SALES_TABLE, {}, 500, pt),
            fetch_all=True,
            max_pages=1000,
        )
        kept = filter_records_by_date(all_items, DATE_FIELD, dr_90)
        schema = get_table_schema(c, APP_TOKEN, SALES_TABLE)
        expanded = expand_links(c, APP_TOKEN, kept, schema)
        print(f"  scanned={len(all_items)}, kept(90d)={len(kept)}, expanded={len(expanded)}")
        report("D", c, t0)

    # --- E: 30d, full path again (separate call, simulating second pass) ---
    dr_30 = build_date_range(tz_name="Asia/Shanghai", range_spec="2026-03-17..2026-04-15")
    with stage("E. records/search 30d date-filter + expand_links (the 'second pass')"):
        c = CountingClient(real)
        t0 = time.perf_counter()
        all_items = paginate_all(
            lambda pt: _search_page(c, SALES_TABLE, {}, 500, pt),
            fetch_all=True,
            max_pages=1000,
        )
        kept = filter_records_by_date(all_items, DATE_FIELD, dr_30)
        schema = get_table_schema(c, APP_TOKEN, SALES_TABLE)
        expanded = expand_links(c, APP_TOKEN, kept, schema)
        print(f"  scanned={len(all_items)}, kept(30d)={len(kept)}, expanded={len(expanded)}")
        report("E", c, t0)

    # --- F: dissect expand cost: pull each target table individually ---
    target_tables = {
        "客户明细": "tblDl4dATw3KB8hP",
        "次卡课包(自动化)": "tblQJn2ylLrZHwLo",
        "教练明细(自动化)": "tblpafyaVfx6xsdM",
        "预约服务(自动化)": "tblmdqLVHX1d7o31",
    }
    for name, tid in target_tables.items():
        with stage(f"F. expand-cost 全表拉 {name} ({tid})"):
            c = CountingClient(real)
            t0 = time.perf_counter()
            items = paginate_all(
                lambda pt: _list_page(c, tid, 500, pt),
                fetch_all=True,
                max_pages=200,
            )
            print(f"  total records = {len(items)}")
            report(f"F.{name}", c, t0)


if __name__ == "__main__":
    main()
