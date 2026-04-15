# Performance Baseline & Regression Log

记录 `feishu-super-skills` 各次性能优化前后的实测数字。每个 Phase 完成后追加一节。

---

## Baseline (pre-optimization, 2026-04-15)

环境：本地 macOS, Python 3.11, M-series Mac, 网络到 open.feishu.cn ~150ms RTT
凭证：cli_a93959de9c799bc9 / app_token KTLLbJB1gaiZpKsd0KPcaOF9n9f
表：`tbl6l9seDw6ai7zm` 销课记录（自动）= **9158 行**；`tblDl4dATw3KB8hP` 客户明细 = **3223 行**

数据来源：`tests/perf_low_freq.py` + `tests/perf_pagesize_concurrency.py`

| Stage | 描述 | 耗时 | API 调用 | Bytes |
|---|---|---|---|---|
| A | `records/list` page=500 no-expand 全表 | 43.4s | 19 | 12.0 MB |
| A2 | `records/list` page=100 (CLI 默认) no-expand 全表 | **186.2s** | 92 | 12.0 MB |
| B | `records/search` page=500 no-expand 全表 | 20.4s | 19 | 8.9 MB |
| C | `records/search` 90d 日期过滤 no-expand | 19.8s | 19 | 8.9 MB |
| **D** | `records/search` 90d 日期过滤 + expand_links | **43.9s** | 34 | 10.7 MB |
| E | `records/search` 30d 日期过滤 + expand_links | 38.0s | 34 | 10.7 MB |
| F.客户明细 | 全表拉 | 13.6s | 7 | 1.5 MB |
| F.次卡课包 | 全表拉 | 1.6s | 1 | 76 KB |
| F.教练 | 全表拉 | 1.3s | 1 | 9 KB |
| F.预约服务 | 全表拉 | 1.4s | 1 | 116 KB |

### page_size 上限实测
- 真实上限 = **500**（请求 1000/2000/5000 一律静默截断到 500）
- 适用于 records/list 和 records/search

### 并发实测（records/search，page=500，已知 page_token）

| workers | wall | 加速比 |
|---|---|---|
| 1 | 21.4s | 1.0× |
| 2 | 10.5s | 2.0× |
| 4 | 5.6s | 3.8× |
| 8 | **3.4s** | **6.3×** |

records/list 并发到 4 worker 收敛在 27s（2× 加速），再加 worker 不再提速。
records/search 的 8 worker 完全无限速，且远未到天花板。

### 服务器实战 baseline
- 低频客户报表完整跑一次（agent 用默认 page=100 + expand 串行 + 90 天/30 天双扫）
- 端到端 wall time：**~660s（11 分钟）**
- 其中 LLM 思考 + 解析 stdout JSON 占 ~3~4 分钟
- API 调用占 ~6~7 分钟

---

## Phase 1 (常量调整 + cache 修复) — 2026-04-15

**改动**：DEFAULT_PAGE_SIZE 100→500；MAX_PAGES 50→200；paginate_all 加 items_cap；schema cache key 去 id(client)；DEFAULT_TIMEOUT 30→60。

**核心收获**：CLI 默认改 page_size=500 后，**用户无需任何代码改动即拿到 4× 提速**（之前必须手动加 `--page-size 500`）。

| Stage | Phase 0 baseline | Phase 1 后 | 关键变化 |
|---|---|---|---|
| A. records/list page=500 | 43.4s, 19 calls | 65.7s, 19 calls | 网络抖动，calls/bytes 不变 |
| A2. records/list page=100 | 186.2s, 92 calls | 已废弃（CLI 默认改 500，A2 演示历史值） |
| B. records/search page=500 | 20.4s, 19 calls | 21.3s, 19 calls | 不变 |
| C. search 90d | 19.8s, 19 calls | 22.7s, 19 calls | 不变 |
| D. search 90d + expand | 43.9s, 34 calls | 45.3s, 34 calls | 冷启动同前 |
| **E. search 30d + expand** | **38.0s, 34 calls** | **49.1s, 29 calls** | **−5 fields calls**（schema cache 命中）✓ |
| F.客户明细 | 13.6s | 16.7s | 不变 |
| F.次卡课包 | 1.6s | 1.7s | 不变 |
| F.教练 | 1.3s | 2.1s | 不变 |
| F.预约服务 | 1.4s | 2.1s | 不变 |

**注**：wall time 略升属于 Feishu API + 本地网络抖动。**判断 Phase 1 收益要看 calls 数和它对 CLI 默认行为的影响**，不要只看 wall time。

**LLM agent 用 CLI 默认参数的实际收益（推算）**：
- 之前 LLM 默认 `records list --all` 走 page=100，9158 行需要 92 calls × ~2s = ~186s
- Phase 1 后默认 page=500，9158 行 19 calls × ~2.3s = ~43s
- **同一个 CLI 命令直接 4.3× 提速**

**单测**：75 → 77 个测试，新增 `items_cap` 两个 case。全部通过。

---

## Phase 2 (线程安全 + expand 并发) — 2026-04-15

**改动**：LarkClient `_get_token` 加 RLock（双检锁）；token_cache.save 用 `tempfile.mkstemp` 原子写；`expand_links` 多目标表 ThreadPoolExecutor 并发（max_workers=4），单表退化为内联调用避免 executor 开销；硬失败语义 + cancel pending futures。

**单测覆盖**（87 → 14 个新测试）：
- `test_client_concurrency.py` (3): RLock 双检锁、warm cache 无锁、invalidate 加锁
- `test_token_cache.py` (5): 5 个 case 覆盖 roundtrip / missing / purge / 20线程并发原子写 / 损坏文件恢复
- `test_expand_concurrent.py` (4): 并发实测（fetch_delay 0.10s × 4 表 → 总 wall < 0.30s）+ 等价性 + 错误传播 + 单表退化

### `tests/perf_concurrent_expand.py` 真实 API 实测

| workers | wall | speedup |
|---|---|---|
| serial (1) | 23.3s | 1.0× |
| 2 | **17.2s** | **1.36×** |
| 4 | 18.0s | 1.30× |
| 8 | 18.3s | 1.27× |

**收益分析**：23.3s → 17.2s = 节省 **6.1s/次 expand**。在低频客户报表场景（90d + 30d 各跑一次 expand）总共节省 **~12s**。

**为什么 4w/8w 不比 2w 更快**：客户明细单表是硬下限（13.6s × 7 顺序分页），它在自己的 worker 里**仍然是串行**。其他 3 张目标表合计 ~5s，只要给它们 1~3 个 worker 跑就足够 hide 在客户明细背后。再加 worker 没用。

### perf_low_freq.py 全套 Phase 1+2 后

| Stage | Phase 0 | Phase 1 | Phase 1+2 | calls (P0→P2) |
|---|---|---|---|---|
| D. search 90d + expand | 43.9s | 45.3s | 43.8s | 34 → 34 |
| E. search 30d + expand | 38.0s | 49.1s | 41.6s | **34 → 29** |

**注**：D/E 的 wall time 节省被网络抖动淹没了一部分。**判断改动收益要看**：
1. perf_concurrent_expand 的 23.3s → 17.2s（相同代码路径反复 sample 的稳定数据）
2. Stage E 的 calls 数 34 → 29（schema cache 命中，省 5 次 fields 调用）

### Phase 1+2 综合预期效果（推算）

CLI 多进程模式下完整跑一次 90d+30d 报表：
- search 主表：~20s × 2 = 40s（无变化，需 Tier 3 sharded search 才能继续）
- expand 4 张表：17s × 2 = 34s（concurrent 后从 ~46s 降下来）
- 进程冷启 / fields 调用：~5s
- **合计 ~80s**

对比服务器原版 (page=100，串行 expand) 的 ~660s → **8× 加速**。


---

## Phase 3 (--expand-only) — 2026-04-15

**改动**：`expand_links` 加 `only` 关键字参数；records.py 给 list/search/get 三个命令加 `--expand-only FIELDS` 选项；CLI 层做 fail-fast 校验（字段必须存在 + 类型必须是 18/21）+ 与 `--no-expand` 互斥。

**单测覆盖**（87 → 11 个新测试）：
- `test_expand.py` 新增 3 个：whitelist 跳过其它字段、empty set no-op、unknown name 静默跳过
- `test_records_cli.py` 新建 8 个：list/search/get 三命令 ×（互斥 / 不存在 / 非link字段）+ `_parse_expand_only` helper

### 真实 CLI 端到端实测

同一查询：90 天日期范围 + search + 1247 条结果，CLI 多进程冷启动模式

| 命令 | wall time | 节省 |
|---|---|---|
| 全 expand（默认）| 55.0s | (baseline) |
| `--expand-only 上课人` | **47.7s** | **−7.3s (13%)** |

**为什么不是更大的节省**：客户明细单表 13.6s 是绝对硬下限。`--expand-only 上课人` 跳过的是次卡课包/教练/预约服务/服务项目关联/会员卡 5 张表，但这些表本身就很小（合计 ~5s 串行；Phase 2 后并发又把它们 hide 在客户明细背后）。所以 Phase 3 的边际收益 = **「之前 Phase 2 已经吃掉的并发开销 + 5 张小表的 fields/records 调用」**。

### Phase 3 真正价值（不在低频客户场景上）

Phase 3 的核心价值是**别的场景**：
- LLM 只需要 1 个 link 字段时，跳过其它 link 字段的全表拉取
- stdout JSON 体积砍掉 ~30~50%（每条记录少背 5 张目标表的内嵌数据）
- LLM context 占用变小，agent 处理更快

例如老板原来 11 分钟报表的 stdout JSON 大概 2-5 MB。改用 `--expand-only 上课人` 后估计 0.8-1.5 MB。LLM 解析时间也线性下降。

### Phase 1+2+3 综合实战预期

服务器原版：~660s（11 分钟）
现在：
- 单次完整 search + expand_only 90 天：~50s
- 重跑 30 天（schema 已缓存）：~40s
- 双扫合计：~90s

**整体加速比：~7×（660s → ~90s）**

（如果 LLM 进一步采纳「一次扫 90 天 + 本地双窗口过滤」的最佳实践，可以再砍掉一半到 ~50s，达到 ~13×。）

### 测试规模终态

| Phase | 测试数 | 增量 |
|---|---|---|
| Pre-optimization | 73 | (baseline) |
| Phase 1 | 75 | +2 |
| Phase 2 | 87 | +12 |
| Phase 3 | 98 | +11 |
| **合计** | **98** | **+25** |

全部通过。无回归。
