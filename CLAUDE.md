# CLAUDE.md — feishu-super-skills 行为指引

本文件给运行本 Skill 的 LLM（尤其是 Claude Code）提供**强制性**行为规则。阅读 `SKILL.md` 了解能力概览，本文档优先级高于 SKILL.md 中的非强制描述。

## 基本调用方式

本 Skill 是一个 uv 管理的 Python CLI，入口：

```bash
uv run feishu-super <group> <command> [args]
# 或
./scripts/feishu.sh <group> <command> [args]
```

**所有命令的 JSON 结果通过 stdout 输出**；预览、表格、错误信息通过 stderr 输出。解析数据时请只读 stdout。

## 写操作权限围栏（**HARD RULE**）

下列命令属于破坏性写操作：

- `records create` / `update` / `delete` / `batch-create` / `batch-update` / `batch-delete`
- `fields add` / `delete`
- `tables create` / `delete`

**规则**：

1. 第一次调用**禁止**加 `--confirm`。CLI 会打印 `DRY RUN` 预览并退出码 2。这是设计，不是错误。
2. 看到退出码 2 时，应把 stderr 中的预览内容原样呈现给用户，然后用中文主动询问：「以上操作是否确认执行？」
3. 只有在用户用明确肯定表达（"确认"、"执行"、"yes"、"ok"、"继续"等）回复后，才能加 `--confirm` 重跑。
4. **例外**：如果用户在一次对话开头就明确说过"直接执行别问我"、"自动执行"、"auto confirm"类指令，允许第一次就带 `--confirm`。但每个新会话默认不继承这个豁免。
5. **批量操作**尤其谨慎：批删/批改 > 20 条时，即使用户给了豁免，也建议再次确认次数与目标表。

**不要绕过**：不要尝试用 `echo "yes" | ...` 或自己实现确认逻辑。围栏在 CLI 层强制，不存在跳过路径。

## 查询策略

- 优先使用 `records search` 而不是 `records list`。`search` 可带 filter/sort/field_names，响应更小。
- 使用 `--fields` 投影只取用户需要的列，避免把飞书返回的大 JSON 灌进 LLM context。
- `--fuzzy` 默认走服务端 filter（对所有 Text/Phone/Url 字段 OR contains）。只有在字段都是公式/链接等非文本类型时才用 `--client-fuzzy`。
- 聚合多页时用 `--all`，但**主动加 `--show N`** 控制 JSON 输出大小（默认只展示前 10 条 + 总数）。
- 不要无脑 `records list --all` 抓整表 —— 飞书表动辄数千记录。
- **page_size 默认已经是 500**（飞书 API 真上限）。**不要再手动加 `--page-size 100`**，那是上一版本的反模式。
- **`--all` 默认上限**：500 行/页 × 200 页 = **100k 行**。超过会 stderr warn 并截断。如果实际表更大，必须用 `--filter` / `--where` 收窄。

## 已知的飞书坑 & 本 Skill 如何帮你避开（**重要**）

### 坑 1：`records search` 的 Link 字段比 `records list / get` 残缺

飞书 OpenAPI 的事实：`records/search` 对 SingleLink/DuplexLink 字段只返回
`{"link_record_ids":["rec..."]}` —— 没有 `text`、没有 `table_id`、没有任何
目标表的字段值。`records/list` 和 `records/:id` 则返回完整的
`{"record_ids", "table_id", "text", "text_arr", "type"}`。

**本 Skill 已默认开启 Link 字段自动展开**，并且**在此基础上再补一层**：

```json
"教练": [{
  "record_ids": ["rec..."],
  "table_id": "tbl...",
  "text": "田阳",
  "text_arr": ["田阳"],
  "type": "text",
  "linked_records": [          // ⭐ 这是本 Skill 加的
    {
      "record_id": "rec...",
      "fields": { /* 目标表该条记录的全部字段 */ }
    }
  ]
}]
```

**意味着**：跨表取值时**不需要再发一轮 `records get`**。想要"客户储值余额"、
"次卡剩余次数"、"教练姓名"、"服务名称"？直接从 `fields["上课人"][0]["linked_records"][0]["fields"]["储值余额（元）"]` 里读即可。

**默认行为**：`records search` / `list` / `get` 全部自动展开**所有** Link 字段。
expand 内部会**并发**拉取目标表（最多 4 worker，httpx 线程安全 + RLock token 缓存）。

**精确控制（重要新功能）**：

- `--no-expand`：完全不展开（只想看 record_id）
- `--expand-only 字段A,字段B`：**只**展开列出的 link 字段，跳过其它。**优先用这个**。

为什么 `--expand-only` 重要：源表如果有 6 个 link 字段指向 4 张目标表，默认 expand
会把这 4 张目标表**全部**全表拉一次。但用户通常只需要 1~2 个 link 字段的关联数据。
显式 `--expand-only 上课人` 能：
- 跳过 3~5 张目标表的 records + fields 调用（每张 2~15 秒不等）
- stdout JSON 体积砍掉 30~50%
- LLM context 占用线性下降

**`--expand-only` 校验是 fail-fast 的**：字段名不存在 → exit 2；字段类型不是
SingleLink/DuplexLink → exit 2。直接根据 stderr 错误信息修正字段名。

**与 `--no-expand` 互斥**：同时传两个 → exit 2。

**限制**：只展开 SingleLink (type=18) 和 DuplexLink (type=21)。Lookup
(type=19) / Formula (type=20) 不展开。展开只做**单层**，不做递归。

### 坑 2：`records search` 对 DateTime 字段的范围 filter 会报错

飞书事实：`records/search` 的 filter 对 DateTime 字段（type=5）使用
`isGreater` / `isGreaterEqual` / `isLess` / `isLessEqual` 都会返回
`code=1254018 InvalidFilter`。**只有 `is`（等于某个单点）可用**。

**规避方案**：用这组语义日期参数（`records search` 专属）：

```
--date-field <DateTime字段名>      # 必填：指定作用字段
--date-on YYYY-MM-DD               # 某一天
--date-range START..END            # 区间，YYYY-MM-DD 或毫秒
--date-today / --date-tomorrow / --date-yesterday   # 语义快捷
--tz Asia/Shanghai                 # 时区（默认 Asia/Shanghai）
```

CLI 会：
1. 预拉 schema 验证 `--date-field` 确实是 DateTime
2. 按指定时区计算毫秒区间 `[start_ms, end_ms)`
3. 用 `records search` 拉全表（自动开 `--all`，1000 页上限）
4. 本地过滤出落在区间内的记录
5. 对剩余结果跑 Link 展开

**不要手写 `--where '日期 > 1712000000000'`**。如果 DSL 感知到你这么做
（且传了 schema），会主动抛 DslError 阻止发送。

### 坑 3：`--date-field` + 大表性能

日期筛选走的是"客户端过滤"：必须先把所有页拉完才能筛。对 10 万行以上的
表，一次命令可能花几分钟。如果你能用 `--where` 预先缩小范围（比如加一个
服务端可支持的条件 `状态 = active`），优先这么做，把日期放最后。

### 坑 4：避免重复扫表

如果业务需要"窗口 A（90 天）和窗口 B（30 天）的差集 / 比例 / 计数对比"，
**永远只扫一次（取较大的窗口），本地用 Python 做双过滤**。绝不要跑两次 search。
30 天数据是 90 天的子集，重扫一次浪费同等的网络时间和 API 配额。

## 典型跨表报表任务的推荐写法

### 模式 A：次日预约提醒（需要全部 link 字段）

```bash
uv run feishu-super records search <销课表> \
  --date-field 日期 --date-tomorrow --tz Asia/Shanghai \
  --show 0
```
返回的 JSON 里，每条记录的 `教练`、`预约服务`、`上课人`、`次卡课包` 全部
自动展开成 `linked_records`，下游 Python / jq 脚本直接读目标字段即可，
**零额外 API 调用**。

### 模式 B：低频客户报表（只需要客户余额这一项 link 数据）

**首选写法**——只扫一次表，本地双窗口过滤，只展开必要的 link 字段：

```bash
uv run feishu-super records search <销课表> \
  --date-field 日期 --date-range $(date -d '90 days ago' +%F)..$(date +%F) \
  --expand-only 上课人 \
  --show 0 > sales_90d.json

# 然后本地脚本：
# - 90 天 / 30 天双窗口都从 sales_90d.json 过滤，不再调一次 API
# - 客户余额从 fields["上课人"][0]["linked_records"][0]["fields"]["储值余额（元）"] 直接读
```

**反模式**（不要这么写）：
- ❌ 跑两次 search（90 天一次 + 30 天一次）—— 30 天数据是 90 天的子集，纯浪费
- ❌ 不传 `--expand-only` —— 拉出整张教练/服务/课包表却用不到，每次浪费 ~5s + 几百 KB
- ❌ 手动加 `--page-size 100` —— 那是上一版本的反模式，CLI 默认已经是 500

### 模式 C：纯统计型查询（只要 count，不要明细）

只关心总数，**不要展开 link**：
```bash
uv run feishu-super records search <表> \
  --where '状态 = active' --no-expand --show 0
```
`--show 0` 让 stdout 只输出 `{"total": N, "showing": 0, "records": []}`，
零 link 拉取 + 几十字节响应，最快。

## 错误处理

CLI 错误输出统一走 stderr，格式 `飞书 API 错误: code=XXX msg=YYY`。常见 code：

| code | 含义 | 处理 |
| --- | --- | --- |
| 99991400 / 1254607 | 限速 | CLI 内部已指数退避 3 次；如仍报错请告知用户稍后重试 |
| 99991663 / 99991668 | token 无效 | CLI 已自动清缓存重试；若仍失败说明 app_id/secret 配错 |
| 1254001 | app_token 无效 | 让用户核对多维表格 URL 里的 token |
| 1254006 | table_id 不存在 | 提示用户先 `tables list` 定位 |

## 输出精简

返回结果给用户时：

- 只展示 CLI 输出中的关键字段（表名、记录数、首条内容），不要把整段 JSON 粘贴给用户
- 记录字段里的 `record_id` 要保留，方便后续 update/delete 引用
- 涉及大量记录的场景只总结计数，再按需让用户指定细节

## 作用域边界

本 Skill **只**覆盖多维表格（Bitable）。飞书的其他开放能力（IM 消息、云文档、日历、通讯录、视频会议等）不在范围内，遇到这些需求请让用户换用其他合适的工具。
