# feishu-super-skills

面向 LLM agent 的飞书多维表格（Bitable）Python CLI：**Link 字段自动展开**、**日期语义筛选**、**写操作权限围栏**、**高阶查询能力**。

## 特性

- **聚焦一件事**：多维表格 CRUD。不涉及云文档 / IM / 日历。
- **uv + Python 3.11+**：依赖隔离，`uv sync` 即装即跑。
- **Link 字段自动展开** _(v0.2)_：查询结果里的 SingleLink / DuplexLink 字段会自动补齐 `text`，并把目标表该行的**完整字段**嵌入为 `linked_records`。跨表取值无需再调 `records get` —— 彻底解决运营报表任务里"一条记录要发 N 轮 API"的痛点。
- **日期语义参数** _(v0.2)_：`--date-field / --date-on / --date-range / --date-today / --date-tomorrow / --tz`，绕开飞书 `records/search` 对 DateTime 字段范围 filter 不支持（`code=1254018 InvalidFilter`）的限制，自动走本地区间过滤。`--where` DSL 对 DateTime 误用 `>` / `<` 时会**提前报错**，避免 LLM 踩坑。
- **权限围栏**：所有破坏性命令强制要求 `--confirm`，未带 flag 时打印 DRY RUN 预览并退出码 2 — 天然适合 LLM agent 的「预览→请示→执行」流程。
- **高阶查询**：原生 filter JSON / 简化 `--where` DSL / `--sort` / `--fields` 字段投影 / `--fuzzy` 跨文本字段模糊搜索 / `--all` 自动分页聚合（带截断警告）。
- **Token 缓存**：`tenant_access_token` 持久化到 `~/.cache/feishu-super/`，提前 5 分钟刷新。
- **限速自愈**：飞书限速码（`99991400` / `1254607`）自动指数退避重试；写操作**不**重试，避免重复写入。

## 安装

```bash
git clone https://github.com/XiaTiaoQAQ/feishu-super-skills.git
cd feishu-super-skills
uv sync
```

## 配置

复制 `.env.example` 为 `.env`，填入自建应用凭证：

```bash
cp .env.example .env
# 编辑 .env
```

```env
FEISHU_APP_ID=cli_xxxxxxxx
FEISHU_APP_SECRET=xxxxxxxx
FEISHU_APP_TOKEN=               # 可选：目标多维表格 App Token
```

也可以放到调用方项目的 `$PWD/.env`，CLI 会自动检测。解析顺序：

```
CLI 参数 → shell env → $PWD/.env → $PWD/.env.local → skill 目录/.env
```

随时用 `feishu-super env` 查看当前每个变量来自哪个来源。

## 快速上手

```bash
# 环境诊断
uv run feishu-super env

# 列出 app 下的表
uv run feishu-super tables list --app-token <APP_TOKEN>

# 探查字段
uv run feishu-super fields list <TABLE_ID>

# 查询：简化 DSL
uv run feishu-super records search <TABLE_ID> \
    --where '姓名 contains "张" and 状态 = active' \
    --sort '创建时间 desc' \
    --show 20

# 查询：模糊搜索
uv run feishu-super records search <TABLE_ID> --fuzzy "关键词" --all

# 查询：日期语义（次日 / 某一天 / 区间）
uv run feishu-super records search <TABLE_ID> \
    --date-field 日期 --date-tomorrow --tz Asia/Shanghai --show 0
uv run feishu-super records search <TABLE_ID> \
    --date-field 日期 --date-on 2026-04-11 --show 10
uv run feishu-super records search <TABLE_ID> \
    --date-field 日期 --date-range 2026-04-01..2026-04-30 --show 10

# 写操作（第一次不带 --confirm，CLI 会打印预览并退出 2）
uv run feishu-super records create <TABLE_ID> --data '{"姓名":"张三"}'
# 用户确认后再加 --confirm
uv run feishu-super records create <TABLE_ID> --data '{"姓名":"张三"}' --confirm
```

也可以直接用 `scripts/feishu.sh`：

```bash
./scripts/feishu.sh records list <TABLE_ID> --show 5
```

## 命令总览

### 查（无需确认，直接执行）

```
feishu-super env                                    环境诊断
feishu-super tables list  [--app-token T] [--name K]
feishu-super tables get   <table_id>
feishu-super fields list  <table_id> [--name K] [--type N]
feishu-super records list   <table_id> [--all] [--show N] [--no-expand]
feishu-super records get    <table_id> <record_id> [--no-expand]
feishu-super records search <table_id>              核心查询
    [--filter '<raw_json>']                         原生飞书 filter
    [--where '...']                                 简化 DSL（DateTime 字段用范围操作符会报错）
    [--sort 'created_time desc, name asc']
    [--fields 'f1,f2,f3']                           字段投影
    [--fuzzy '关键词']                               跨文本字段 OR contains
    [--date-field 日期 --date-tomorrow]              日期语义（详见下）
    [--date-field 日期 --date-range START..END]
    [--all] [--show 20] [--client-fuzzy] [--no-expand]
```

### Link 字段自动展开（v0.2 默认开启）

所有 `records list / get / search` 返回的 SingleLink / DuplexLink 字段会被自动展开：

```json
"教练": [{
  "record_ids": ["rec..."],
  "table_id": "tbl_coach",
  "text": "田阳",
  "text_arr": ["田阳"],
  "type": "text",
  "linked_records": [{
    "record_id": "rec...",
    "fields": { "教练姓名": "田阳", "电话": "138...", "状态": "正常" }
  }]
}]
```

`linked_records[0].fields` 是目标表该条记录的**完整字段**。跨表取值直接从 JSON 里读，无需再发 `records get`。关闭行为用 `--no-expand`。

### 日期语义（v0.2）

飞书 `records/search` 对 DateTime 字段（type=5）的范围 filter（`isGreater` / `isLess` ...）一律返回 `code=1254018 InvalidFilter`。本 CLI 用以下语义参数替代：

```
--date-field <DateTime字段名>       必填
--date-on YYYY-MM-DD                某一天
--date-range START..END             区间（YYYY-MM-DD 或毫秒）
--date-today / --date-tomorrow / --date-yesterday   语义快捷
--tz Asia/Shanghai                  时区（默认 Asia/Shanghai）
```

CLI 会：预拉 schema 确认字段类型 → 按时区算 `[start_ms, end_ms)` → 自动 `--all` 遍历全表（1000 页上限）→ 本地过滤 → 对剩余结果跑 Link 展开。

### 增 / 删 / 改（必须 `--confirm`）

```
feishu-super records create       <table_id> --data '{...}'        --confirm
feishu-super records update       <table_id> <record_id> --data '{...}' --confirm
feishu-super records delete       <table_id> <record_id>           --confirm
feishu-super records batch-create <table_id> --file records.json   --confirm
feishu-super records batch-update <table_id> --file records.json   --confirm
feishu-super records batch-delete <table_id> --ids rec1,rec2       --confirm
feishu-super fields  add          <table_id> --name X --type 1     --confirm
feishu-super fields  delete       <table_id> <field_id>            --confirm
feishu-super tables  create       --name X [--fields-file f.json]  --confirm
feishu-super tables  delete       <table_id>                       --confirm
```

## 权限围栏工作流

破坏性命令在 CLI 层**硬拒绝**未加 `--confirm` 的调用：

1. LLM 先**不带** `--confirm` 运行命令 → CLI 打印 DRY RUN 预览，退出码 2
2. LLM 把预览呈现给用户，等待明确肯定表达（「确认」「执行」「yes」）
3. 得到确认后，LLM 加 `--confirm` 重跑，CLI 再打印一行审计摘要后真正执行

退出码 2 是**设计信号**，不是错误。详见 `CLAUDE.md`。

## 项目结构

```
feishu-super-skills/
├── SKILL.md                    # LLM 入口（YAML frontmatter）
├── CLAUDE.md                   # LLM 行为规则（权限围栏细节）
├── README.md                   # 本文件
├── pyproject.toml              # uv 项目定义
├── .env.example
├── src/feishu_super/
│   ├── cli.py                  # Typer 主入口
│   ├── config.py               # 多路径 .env 解析
│   ├── client.py               # httpx 客户端 + token 缓存 + 限速重试
│   ├── token_cache.py          # 持久 token 缓存
│   ├── field_types.py          # 字段类型码 → 名称
│   ├── formatters.py           # JSON/Rich 输出
│   ├── guard.py                # --confirm 权限围栏
│   ├── where_dsl.py            # --where DSL → Feishu filter JSON（类型感知）
│   ├── schema.py               # 表 schema 进程内缓存
│   ├── expand.py               # Link 字段自动展开（带 linked_records）
│   ├── date_range.py           # 日期语义 + 时区 + 本地区间过滤
│   └── commands/               # tables / fields / records 子命令
├── scripts/feishu.sh           # 薄包装
├── skills/                     # LLM 子 skill 文档
└── tests/                      # pytest 单测
```

## 开发

```bash
uv sync --extra dev
uv run pytest
```

## License

MIT
