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

| operator | 简化 DSL 形式 | 含义 |
| --- | --- | --- |
| `is` | `=` | 等于 |
| `isNot` | `!=` | 不等于 |
| `isGreater` | `>` | 大于 |
| `isGreaterEqual` | `>=` | 大于等于 |
| `isLess` | `<` | 小于 |
| `isLessEqual` | `<=` | 小于等于 |
| `contains` | `contains` | 文本包含 |
| `doesNotContain` | `not_contains` | 文本不包含 |
| `isEmpty` | `is_empty` | 为空（value 传 `[]`）|
| `isNotEmpty` | `is_not_empty` | 非空 |

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

等价的简化 DSL 写法：

```bash
feishu-super records search tblxxxxxx \
  --where '状态 = active and 创建时间 > 1712000000000' \
  --sort '创建时间 desc' \
  --fields '姓名,手机,创建时间' \
  --show 20
```
