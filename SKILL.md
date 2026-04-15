---
name: feishu-super-skills
description: "飞书多维表格高阶 OpenAPI 技能 — Python 原生（uv 管理）：Link 字段自动展开（并发拉取 + 白名单精控）、日期语义筛选、写操作权限围栏"
version: 0.3.0
---

# feishu-super-skills

一个聚焦「飞书多维表格（Bitable）」的 Skill，Python + uv 实现，面向 LLM agent 场景。

**核心特性**：

- **Link 字段自动展开**（0.2 新）：`records search / list / get` 默认对所有 SingleLink / DuplexLink 字段自动补齐 `text`，并把目标表该行的**完整字段**作为 `linked_records` 嵌入返回。跨表取值无需再调 `records get`，彻底解决"查客户需要再查储值余额、查记录需要再查服务名称"的老问题
- **多目标表并发 expand**（0.3 新）：expand 内部对多张目标表使用 `ThreadPoolExecutor` 并发拉取（默认 4 worker），受限于最慢单表。同时整体性能大幅提升：`page_size` 默认 500（飞书真上限）、`MAX_PAGES` 200（100k 行默认 ceiling）、`schema_cache` 跨 client 复用，服务器实测 11 分钟级报表 → ~50 秒（~13× 加速）
- **`--expand-only` 字段白名单**（0.3 新）：`records list/search/get` 支持 `--expand-only 上课人,会员卡`，只展开指定的 link 字段，跳过其它。用于源表有 6 个 link 字段但只需要 1~2 个的场景，避免无谓的目标表全表拉取 + stdout JSON 膨胀。字段名 / 字段类型 fail-fast 校验（不存在 / 非 SingleLink 都 exit 2），与 `--no-expand` 互斥
- **日期语义参数**（0.2 新）：`--date-field / --date-on / --date-range / --date-today / --date-tomorrow / --date-yesterday / --tz Asia/Shanghai`，绕开飞书 `records/search` 对 DateTime 字段范围 filter 不支持的限制
- **线程安全**（0.3 新）：`LarkClient._get_token` 用 RLock + 双检锁保护（fast path 仍无锁），`token_cache.save` 用 `tempfile.mkstemp + os.replace` 原子写，多线程 / 多进程并发调用均安全
- **高阶查询**：原生 filter JSON、简化 `--where` DSL（DateTime 字段误用范围操作符会**提前报错**）、`--sort`、`--fields` 投影、`--fuzzy` 跨字段模糊搜索、`--all` 自动分页（带截断警告 + `items_cap` 软上限）
- **写操作权限围栏**：CLI 层面强制要求 `--confirm`，未带 flag 时打印 DRY RUN 预览并退出码 2，防止 LLM 擅自写入

## 触发场景

用户提到以下任一关键词时应考虑激活：飞书、多维表格、bitable、lark base、app_token、table_id、字段探查、记录筛选、模糊搜索、飞书查表。

## 环境要求

需要 3 个环境变量（必需 2 个）：

| 变量 | 必需 | 含义 |
| --- | --- | --- |
| `FEISHU_APP_ID` | ✓ | 自建应用的 App ID |
| `FEISHU_APP_SECRET` | ✓ | 自建应用的 App Secret |
| `FEISHU_APP_TOKEN` | × | 目标多维表格的 App Token（可用 `--app-token` 逐次覆盖）|

**自动识别顺序**：`--app-id/--app-secret` CLI 参数 → shell env → `$PWD/.env` → `$PWD/.env.local` → 本 skill 目录下 `.env`。用 `feishu-super env` 一键查看当前来自哪个来源。

## 快速上手

```bash
cd feishu-super-skills
uv sync                                         # 首次安装依赖
uv run feishu-super env                         # 诊断环境
uv run feishu-super tables list --app-token <APP_TOKEN>
```

或使用薄包装：

```bash
./scripts/feishu.sh tables list --app-token <APP_TOKEN>
```

## 命令总览

### 查（安全，可直接执行）

```
feishu-super env                                                  # 环境诊断
feishu-super tables list  [--app-token T] [--name 关键词]
feishu-super tables get <table_id>
feishu-super fields list  <table_id> [--name X] [--type 1]        # 字段探查
feishu-super records list  <table_id> [--all] [--show 10] [--no-expand] [--expand-only F1,F2]
feishu-super records get   <table_id> <record_id> [--no-expand] [--expand-only F1,F2]
feishu-super records search <table_id> \                          # 核心查询
    [--filter '<raw_json>']         # 完整 Feishu filter JSON
    [--where 'name contains "abc" and status = active']  # 简化 DSL
    [--sort 'created_time desc,name asc']
    [--fields 'f1,f2,f3']           # 字段投影
    [--fuzzy '关键词']              # 文本字段 OR contains 模糊搜索
    [--date-field 日期 --date-tomorrow --tz Asia/Shanghai]   # 日期语义
    [--date-field 日期 --date-on 2026-04-11]
    [--date-field 日期 --date-range 2026-04-01..2026-04-30]
    [--all] [--show 20] [--client-fuzzy] [--no-expand]
    [--expand-only 上课人,会员卡]  # 只展开列出的 link 字段，跳过其它（0.3 新）
```

**Link 字段自动展开**（默认开启）：所有 `records search / list / get` 返回的
SingleLink/DuplexLink 字段会自动补齐成：

```json
"教练": [{
  "record_ids": ["rec..."],
  "table_id": "tbl...",
  "text": "田阳",
  "linked_records": [{
    "record_id": "rec...",
    "fields": { /* 目标表该条记录全部字段 */ }
  }]
}]
```

下游脚本读 `fields["上课人"][0]["linked_records"][0]["fields"]["储值余额（元）"]`
即可拿到客户储值余额，**不必再调 records get**。

**精确控制**：
- `--no-expand`：完全关闭 Link 展开
- `--expand-only 上课人`：只展开列出的 link 字段，跳过其它（源表 link 字段多、下游只需要 1~2 个时首选，避免拉无用目标表 + 砍 stdout JSON 30~50%）

### 增 / 删 / 改（**必须走权限围栏**）

```
feishu-super records create   <table_id> --data '{"姓名":"张三"}'  --confirm
feishu-super records update   <table_id> <record_id> --data '{...}' --confirm
feishu-super records delete   <table_id> <record_id> --confirm
feishu-super records batch-create <table_id> --file records.json   --confirm
feishu-super records batch-update <table_id> --file records.json   --confirm
feishu-super records batch-delete <table_id> --ids rec1,rec2       --confirm
feishu-super fields  add     <table_id> --name 姓名 --type 1        --confirm
feishu-super fields  delete  <table_id> <field_id>                  --confirm
feishu-super tables  create  --name 新表 [--fields-file f.json]     --confirm
feishu-super tables  delete  <table_id>                             --confirm
```

## 权限围栏规则（⚠️ LLM 必读）

**任何破坏性命令（create / update / delete / batch-*）第一次调用时都不应该加 `--confirm`。**

流程：

1. LLM 先**不带** `--confirm` 运行命令。CLI 会打印"DRY RUN 预览"并以退出码 2 退出。
2. LLM 把预览内容**原样展示给用户**，并主动询问："上述操作是否确认执行？"
3. 用户明确回复"确认"、"执行"、"yes"、"继续" 等**肯定表达**后，LLM 再加上 `--confirm` 重新运行命令。
4. 如果用户在最开始就说了"别问我直接执行"、"直接干"、"auto confirm"，可以跳过预览直接带 `--confirm`。

> 退出码 2 **不是错误**，是权限围栏设计的一部分。看到 `[GUARD]` 输出时应按上述流程处理，不要把它当作异常吐给用户。

## 子技能

| 子技能 | 侧重 |
| --- | --- |
| [feishu-bitable](skills/feishu-bitable/SKILL.md) | 多维表格全能力（含字段类型码速查与端点清单） |
| [feishu-bitable-query](skills/feishu-bitable-query/SKILL.md) | 查询能力聚焦：DSL、排序、模糊搜索实战 |

## 相关文档

- 字段类型码速查：[`skills/feishu-bitable/references/field-types.md`](skills/feishu-bitable/references/field-types.md)
- `records/search` filter 语法：[`skills/feishu-bitable/references/search-filter.md`](skills/feishu-bitable/references/search-filter.md)
- 所有端点清单：[`skills/feishu-bitable/references/openapi-endpoints.md`](skills/feishu-bitable/references/openapi-endpoints.md)
