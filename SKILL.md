---
name: feishu-super-skills
description: "飞书多维表格高阶 OpenAPI 技能 — Python 原生（uv 管理），支持 App 扫表、字段探查、筛选/排序/模糊搜索，增删改带强制权限围栏"
version: 0.1.0
---

# feishu-super-skills

一个聚焦「飞书多维表格（Bitable）」的 Skill，Python + uv 实现，面向 LLM agent 场景。三大卖点：

- **Python 原生实现**：无外部 CLI 依赖，`uv sync` 即装即跑
- **高阶查询能力**：原生 filter JSON、简化 `--where` DSL、`--sort`、`--fields` 字段投影、`--fuzzy` 跨字段模糊搜索、`--all` 自动聚合分页
- **写操作权限围栏**：CLI 层面强制要求 `--confirm`，未带 flag 时打印预览并退出码 2，防止 LLM 擅自写入

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
feishu-super tables list  [--app-token T] [--name 关键词]          # 列表
feishu-super tables get <table_id>
feishu-super fields list  <table_id> [--name X] [--type 1]        # 字段探查
feishu-super records list  <table_id> [--all] [--show 10]
feishu-super records get   <table_id> <record_id>
feishu-super records search <table_id> \                          # 核心查询
    [--filter '<raw_json>']         # 完整 Feishu filter JSON
    [--where 'name contains "abc" and status = active']  # 简化 DSL
    [--sort 'created_time desc,name asc']
    [--fields 'f1,f2,f3']           # 字段投影
    [--fuzzy '关键词']              # 所有文本字段 OR contains
    [--all] [--show 20] [--client-fuzzy]
```

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
