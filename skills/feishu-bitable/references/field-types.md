# 飞书多维表格字段类型码速查

| 码值 | 名称 | 写入值形式 | 读出值形式 | 备注 |
| --- | --- | --- | --- | --- |
| 1 | Text | `"字符串"` | `"字符串"` | 多行文本 |
| 2 | Number | `123` / `1.5` | `number` | `property.formatter` 控制显示精度 |
| 3 | SingleSelect | `"选项名"` | `"选项名"` 或 `{text:"..."}` | 选项名在 `property.options[].name` |
| 4 | MultiSelect | `["A","B"]` | `string[]` | 同上 |
| 5 | DateTime | 毫秒时间戳 integer | 毫秒时间戳 | 注意时区 |
| 7 | Checkbox | `true` / `false` | `bool` / `null` | `null` 视同 `false` |
| 11 | User | `[{id:"ou_xxx"}]` | `[{id, name, en_name, email}]` | 需 open_id |
| 13 | Phone | `"13800138000"` | `"13800138000"` | 当做文本处理，可 contains |
| 15 | Url | `{link:"https://...",text:"显示文本"}` | 同左 | 可 contains |
| 17 | Attachment | `[{file_token:"xxx"}]` | 附件对象数组 | 上传接口另议，本 skill 未封装 |
| 18 | SingleLink | `["recxxx"]` 或 `"recxxx"` | `[{record_ids, text, type}]` | `property.table_id` 指向目标表 |
| 19 | Lookup | **只读** | 取决于 target field | 不能写 |
| 20 | Formula | **只读** | 取决于公式 | 不能写 |
| 21 | DuplexLink | `["recxxx"]` | `[{record_ids,...}]` | 与 18 类似，双向 |
| 22 | Location | `{address, full_address, lat, lng}` | 同左 | - |
| 23 | Group | `[{id:"oc_xxx"}]` | 群对象数组 | - |
| 1001 | CreatedTime | **只读（自动）** | 毫秒时间戳 | - |
| 1002 | ModifiedTime | **只读（自动）** | 毫秒时间戳 | - |
| 1003 | CreatedUser | **只读（自动）** | user 对象 | - |
| 1004 | ModifiedUser | **只读（自动）** | user 对象 | - |
| 1005 | AutoNumber | **只读（自动）** | 字符串 | - |

## 只读字段集合

本 CLI 的 `fields add` 会拒绝只读类型 (19, 20, 1001-1005)。

## 文本类字段集合（用于 --fuzzy）

`--fuzzy` 会向 Text (1)、Phone (13)、Url (15) 这三类字段 OR contains 展开。如果需要覆盖其他类型，用 `--client-fuzzy` 本地过滤。

## property 常见字段

- **SingleSelect / MultiSelect**: `{options: [{name, color}]}`
- **SingleLink / DuplexLink**: `{table_id: "tbl_xxx", multiple: true}`
- **Number**: `{formatter: "0.00"}` 或 `"0"`
- **DateTime**: `{date_formatter: "yyyy/MM/dd", auto_fill: false}`
