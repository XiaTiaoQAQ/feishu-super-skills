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
