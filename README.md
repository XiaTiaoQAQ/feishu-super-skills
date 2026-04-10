# feishu-super-skills

面向 LLM agent 的飞书多维表格（Bitable）Python CLI：带**写操作权限围栏**和**高阶查询能力**。

## 特性

- **聚焦一件事**：多维表格 CRUD。不涉及云文档 / IM / 日历。
- **uv + Python 3.11+**：依赖隔离，`uv sync` 即装即跑。
- **权限围栏**：所有破坏性命令强制要求 `--confirm`，未带 flag 时打印 DRY RUN 预览并退出码 2 — 天然适合 LLM agent 的「预览→请示→执行」流程。
- **高阶查询**：原生 filter JSON / 简化 `--where` DSL / `--sort` / `--fields` 字段投影 / `--fuzzy` 跨文本字段模糊搜索 / `--all` 自动分页聚合（带截断警告）。
- **Token 缓存**：`tenant_access_token` 持久化到 `~/.cache/feishu-super/`，提前 5 分钟刷新。
- **限速自愈**：Feishu 限速码（`99991400` / `1254607`）自动指数退避重试；写操作**不**重试，避免重复写入。

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
feishu-super records list   <table_id> [--all] [--show N]
feishu-super records get    <table_id> <record_id>
feishu-super records search <table_id>              核心查询
    [--filter '<raw_json>']                         原生 Feishu filter
    [--where 'name contains "abc" and status = active']  简化 DSL
    [--sort 'created_time desc, name asc']
    [--fields 'f1,f2,f3']                           字段投影
    [--fuzzy '关键词']                               跨文本字段 OR contains
    [--all] [--show 20] [--client-fuzzy]
```

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
│   ├── where_dsl.py            # --where DSL → Feishu filter JSON
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
