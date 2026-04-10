---
name: feishu-bitable-query
description: "飞书多维表格查询能力聚焦 — filter/sort/投影/模糊搜索/分页聚合实战手册"
version: 0.1.0
---

# feishu-bitable-query 子技能

本子技能聚焦在**查**。激活词：飞书查表、多维表格筛选、bitable search、模糊搜索飞书、飞书表格排序。

只涉及只读命令，不会触发权限围栏。

## 命令速查

```bash
# 最基础：分页列表（不推荐，数据量大）
feishu-super records list <table_id> --show 10

# 核心命令：search（有过滤/排序/投影/模糊）
feishu-super records search <table_id> \
    [--filter '<json>' | --where 'DSL']  \
    [--sort '字段 desc, 字段 asc']        \
    [--fields 'f1,f2,f3']                \
    [--fuzzy '关键词']                    \
    [--all] [--page-size 100] [--show 20] \
    [--client-fuzzy]
```

## 四种查询模式

### 1. 原生 filter JSON（最灵活）

当需要 `and`/`or` 混用、嵌套组合、复杂操作符时使用：

```bash
feishu-super records search tblxxx \
  --filter '{
    "conjunction":"and",
    "conditions":[
      {"field_name":"状态","operator":"is","value":["active"]},
      {"field_name":"余额","operator":"isGreaterEqual","value":["100"]}
    ]
  }'
```

### 2. 简化 DSL `--where`（开发体验最好）

覆盖 80% 单层场景：

```bash
feishu-super records search tblxxx \
  --where '状态 = active and 余额 >= 100'
```

DSL 语法细节见 [../feishu-bitable/references/search-filter.md](../feishu-bitable/references/search-filter.md)。

### 3. 模糊搜索 `--fuzzy`（跨字段）

在所有 **Text / Phone / Url** 类型字段上做 OR contains：

```bash
feishu-super records search tblxxx --fuzzy "138" --all --show 20
```

CLI 会先调 `fields list` 探测文本字段，再组装 OR filter。如果字段全是链接/公式类型，用 `--client-fuzzy` 回退到客户端过滤：

```bash
feishu-super records search tblxxx --fuzzy "关键词" --all --client-fuzzy
```

### 4. 排序 + 投影

```bash
feishu-super records search tblxxx \
  --where '类型 = "报名"' \
  --sort '创建时间 desc, 金额 desc' \
  --fields '学员,课程,金额,创建时间' \
  --show 20
```

`--fields` 可以显著减小返回体积，建议只取真正需要的列。

## 分页策略

- 不带 `--all`：只拉第一页（默认 100 条）
- 带 `--all`：自动循环 `page_token` 拉取全部，**最多 50 页**（即 5000 条）以防失控
- `--page-size` 调整单页大小（最大 500）

返回的 JSON 总是包装成：

```json
{
  "total": 123,        // 聚合后的总条数
  "showing": 10,       // 实际展示的条数
  "records": [...]     // 仅前 N 条
}
```

## 字段探查（必做第一步）

**任何复杂查询之前，先 `fields list <table_id>`** —— 飞书的 filter 要求用**字段显示名**，而不是 field_id，拼错一个字就会返回空集。

```bash
feishu-super fields list tblxxxxxx --show 50
```

## 常见陷阱

1. **字段名带空格/中文**：在 DSL 中用双引号包起来 `"状态 (冗余)" = active`
2. **数字字段传字符串**：Feishu filter value 数组里的元素统一用字符串，CLI 不做转换
3. **DateTime 要毫秒**：不要传 ISO 字符串，传 unix 毫秒整数字符串
4. **or 混 and**：简化 DSL 不支持，用原生 `--filter`
