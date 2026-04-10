---
name: feishu-bitable
description: "飞书多维表格全能力 — 表/字段/记录 CRUD，带字段类型码速查与端点清单"
version: 0.1.0
---

# feishu-bitable 子技能

覆盖本 Skill 支持的全部多维表格能力。激活词：飞书多维表格、bitable、飞书表格、app_token、table_id。

## 能力矩阵

| 模块 | list | get | search | create | update | delete |
| --- | --- | --- | --- | --- | --- | --- |
| tables | ✓ | ✓ | — | ✓（需 confirm）| — | ✓（需 confirm）|
| fields | ✓ | — | — | ✓（需 confirm）| — | ✓（需 confirm）|
| records | ✓ | ✓ | ✓ | ✓（需 confirm）| ✓（需 confirm）| ✓（需 confirm）|
| records/batch-* | — | — | — | ✓ | ✓ | ✓ |

## 关键参考

- 字段类型码速查：[references/field-types.md](references/field-types.md)
- Search filter/sort 语法：[references/search-filter.md](references/search-filter.md)
- 所有 Feishu 端点清单：[references/openapi-endpoints.md](references/openapi-endpoints.md)

## 典型流程

1. **定位资源**：`tables list --app-token <T>` → 拿到目标 `table_id`
2. **了解结构**：`fields list <table_id>` → 确认字段名与类型码
3. **查询数据**：`records search <table_id> --where ... --sort ... --show 10`
4. **写入**（如需）：
   - 先 dry-run：`records create <table_id> --data '{...}'`（不带 --confirm）
   - 把 CLI 打印的预览呈现给用户
   - 用户确认后加 `--confirm` 重跑

## 注意事项

- **只读字段不能写**：Lookup (19)、Formula (20)、CreatedTime (1001)、ModifiedTime (1002)、CreatedUser (1003)、ModifiedUser (1004)、AutoNumber (1005)。`fields add` 命令会直接拒绝。
- **SingleLink / DuplexLink** 写入时传对方表的 record_id 数组（字符串数组即可），读取时是嵌套对象。
- **DateTime** 字段使用毫秒时间戳（integer）。
- **SingleSelect/MultiSelect** 写入时传 option 名称字符串（或字符串数组）。
