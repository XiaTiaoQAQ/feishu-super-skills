"""Real-API benchmark for the concurrent expand_links path.

Compares serial vs concurrent fetch of 4 target tables against the production
Feishu base, measuring total wall time and worst-case single-table cost.

Usage:
    uv run python tests/perf_concurrent_expand.py

Requires .env with FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_APP_TOKEN.
"""
from __future__ import annotations

import copy
import time

from feishu_super import expand as expand_mod
from feishu_super import schema as schema_module
from feishu_super.client import LarkClient
from feishu_super.config import resolve_config
from feishu_super.expand import expand_links
from feishu_super.schema import get_table_schema

APP_TOKEN = "KTLLbJB1gaiZpKsd0KPcaOF9n9f"
SALES_TABLE = "tbl6l9seDw6ai7zm"


def _build_records(client: LarkClient) -> list[dict]:
    """Pull a small set of source records that touch all 4 link fields.

    We use page_size=100 + only one page so the measurement focuses on the
    expand step, not the source-table fetch.
    """
    path = f"/bitable/v1/apps/{APP_TOKEN}/tables/{SALES_TABLE}/records"
    data = client.get(path, params={"page_size": 100}).get("data") or {}
    return list(data.get("items") or [])


def _run(label: str, max_workers: int, client: LarkClient, records: list[dict]):
    print(f"\n## {label}: max_workers={max_workers}")
    schema_module.clear_cache()  # cold start each run, fair comparison
    expand_mod.EXPAND_MAX_WORKERS = max_workers
    schema = get_table_schema(client, APP_TOKEN, SALES_TABLE)
    # Deep-copy because expand_links mutates records in place; without this,
    # the second run sees already-enriched records and returns instantly.
    fresh_records = copy.deepcopy(records)
    t0 = time.perf_counter()
    expand_links(client, APP_TOKEN, fresh_records, schema)
    dt = time.perf_counter() - t0
    print(f"  elapsed = {dt:.2f}s")
    return dt


def main():
    cfg = resolve_config()
    client = LarkClient(
        app_id=cfg.require("FEISHU_APP_ID"),
        app_secret=cfg.require("FEISHU_APP_SECRET"),
    )

    print("== Pulling source records once ==")
    t0 = time.perf_counter()
    records = _build_records(client)
    print(f"  {len(records)} source records in {time.perf_counter() - t0:.2f}s")

    if not records:
        print("  ! no records returned — abort")
        return

    saved_default = expand_mod.EXPAND_MAX_WORKERS
    try:
        # Serial baseline (max_workers=1 → falls into the inline path).
        t_serial = _run("Serial baseline", 1, client, records)
        # Concurrent runs.
        t_2 = _run("Concurrent", 2, client, records)
        t_4 = _run("Concurrent", 4, client, records)
        t_8 = _run("Concurrent", 8, client, records)
    finally:
        expand_mod.EXPAND_MAX_WORKERS = saved_default

    print("\n== Summary ==")
    print(f"  serial  (1w): {t_serial:.2f}s")
    print(f"  parallel 2w: {t_2:.2f}s  speedup={t_serial / t_2:.2f}×")
    print(f"  parallel 4w: {t_4:.2f}s  speedup={t_serial / t_4:.2f}×")
    print(f"  parallel 8w: {t_8:.2f}s  speedup={t_serial / t_8:.2f}×")
    print()
    print("  Note: the parallel floor is bounded by the slowest target table")
    print("  (客户明细 ~13.6s on this base). Speedup will plateau there.")


if __name__ == "__main__":
    main()
