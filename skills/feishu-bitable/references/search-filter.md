# records/search filter & sort 速查

本 CLI 的 `records search` 命令透传 Feishu 的 `POST /bitable/v1/apps/:app_token/tables/:table_id/records/search` 端点。

## 原生 filter JSON 结构

```json
{
  "conjunction": "and",
  "conditions": [
    {
      "field_name": "姓名",
      "operator": "contains",
      "value": ["张"]
    },
    {
      "field_name": "状态",
      "operator": "is",
      "value": ["active"]
    }
  ]
}
```

- `conjunction`: `"and"` 或 `"or"`
- `conditions[].field_name`: 字段的**显示名称**（不是 field_id）
- `conditions[].operator`: 见下表
- `conditions[].value`: 始终是**数组**，即使只有一个值

## 支持的 operator

| operator | 简化 DSL 形式 | 含义 | DateTime (type=5) 可用 |
| --- | --- | --- | --- |
| `is` | `=` | 等于 | ✓ |
| `isNot` | `!=` | 不等于 | ✓ |
| `isGreater` | `>` | 大于 | ❌ 报 InvalidFilter |
| `isGreaterEqual` | `>=` | 大于等于 | ❌ 报 InvalidFilter |
| `isLess` | `<` | 小于 | ❌ 报 InvalidFilter |
| `isLessEqual` | `<=` | 小于等于 | ❌ 报 InvalidFilter |
| `contains` | `contains` | 文本包含 | — |
| `doesNotContain` | `not_contains` | 文本不包含 | — |
| `isEmpty` | `is_empty` | 为空（value 传 `[]`）| ✓ |
| `isNotEmpty` | `is_not_empty` | 非空 | ✓ |

### ⚠️ DateTime 字段范围筛选

飞书 `records/search` 对 DateTime 字段（type=5）使用 `isGreater` / `isLess`
系列会一律返回 `code=1254018 InvalidFilter`。**本 Skill 的 `--where` DSL
在知道字段类型时会提前报错**，避免你把这个错误发给飞书。

**正确做法**：用语义日期参数替代：

```bash
# 次日（按 Asia/Shanghai 时区）
feishu-super records search <table> \
  --date-field 日期 --date-tomorrow --tz Asia/Shanghai

# 某一天
feishu-super records search <table> \
  --date-field 日期 --date-on 2026-04-11

# 日期区间
feishu-super records search <table> \
  --date-field 日期 --date-range 2026-04-01..2026-04-30
```

这些参数会：先预拉 schema 验证字段确实是 DateTime → 按时区计算
`[start_ms, end_ms)` 区间 → 自动开 `--all` 拉取全部记录（1000 页上限）→
本地按字段值做区间过滤 → 对剩余记录跑 Link 展开。

## 简化 `--where` DSL

```
name contains "abc" and status = active
姓名 contains "张" and 年龄 >= 18
备注 is_not_empty
```

**限制**：

- 顶层 and/or 不能混用（全 and 或全 or）
- 不支持嵌套括号
- 不支持 IN 列表（多值要拆成多条 or）
- 不能表达的情况请用 `--filter '<原生 JSON>'`

## sort 语法

原生 JSON：

```json
[
  {"field_name": "创建时间", "desc": true},
  {"field_name": "姓名", "desc": false}
]
```

CLI 简写（`--sort`）：

```
--sort '创建时间 desc, 姓名 asc'
```

## 字段投影

```
--fields '姓名,年龄,状态'
```

会转为 `body.field_names = [...]`。响应中 `fields` 只会包含这几个 key，节省 token。

## 实战示例

```bash
# 最近 7 天创建的、状态为 active 的用户，按创建时间倒序，前 20 条
feishu-super records search tblxxxxxx \
  --filter '{
    "conjunction":"and",
    "conditions":[
      {"field_name":"状态","operator":"is","value":["active"]},
      {"field_name":"创建时间","operator":"isGreater","value":["1712000000000"]}
    ]
  }' \
  --sort '创建时间 desc' \
  --fields '姓名,手机,创建时间' \
  --show 20
```

等价的简化 DSL 写法（注意："创建时间" 是 CreatedTime 1001 类型，不是 DateTime 5，所以 `>` 对它可用；**普通 DateTime 字段不要这么写**）：

```bash
feishu-super records search tblxxxxxx \
  --where '状态 = active and 创建时间 > 1712000000000' \
  --sort '创建时间 desc' \
  --fields '姓名,手机,创建时间' \
  --show 20
```

DateTime (type=5) 的正确写法（如"日期"列）：

```bash
feishu-super records search tblxxxxxx \
  --where '状态 = active' \
  --date-field 日期 --date-range 2026-04-01..2026-04-30 \
  --sort '日期 desc' \
  --show 20
```

## Link 字段自动展开（默认开启）

查询结果里所有 SingleLink / DuplexLink 字段会自动补齐成：

```json
"教练": [{
  "record_ids": ["rec..."],
  "table_id": "tbl...",
  "text": "田阳",
  "text_arr": ["田阳"],
  "linked_records": [{"record_id": "rec...", "fields": { /* 目标表完整字段 */ }}]
}]
```

`linked_records[0].fields` 包含目标表该条记录的**全部字段**，跨表取值无需
额外调 `records get`。关闭该行为用 `--no-expand`。
