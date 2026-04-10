# 本 Skill 用到的 Feishu Open Platform 端点清单

Base URL: `https://open.feishu.cn/open-apis`

## 鉴权

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/auth/v3/tenant_access_token/internal` | 换取 tenant_access_token；本 CLI 持久缓存到 `~/.cache/feishu-super/`，5 分钟预刷新 |

## 表（Tables）

| 方法 | 路径 | CLI 命令 |
| --- | --- | --- |
| GET | `/bitable/v1/apps/{app_token}/tables` | `tables list` |
| POST | `/bitable/v1/apps/{app_token}/tables` | `tables create` |
| DELETE | `/bitable/v1/apps/{app_token}/tables/{table_id}` | `tables delete` |

## 字段（Fields）

| 方法 | 路径 | CLI 命令 |
| --- | --- | --- |
| GET | `/bitable/v1/apps/{app_token}/tables/{table_id}/fields` | `fields list` |
| POST | `/bitable/v1/apps/{app_token}/tables/{table_id}/fields` | `fields add` |
| DELETE | `/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}` | `fields delete` |

## 记录（Records）

| 方法 | 路径 | CLI 命令 |
| --- | --- | --- |
| GET | `/bitable/v1/apps/{app_token}/tables/{table_id}/records` | `records list` |
| GET | `/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}` | `records get` |
| POST | `/bitable/v1/apps/{app_token}/tables/{table_id}/records/search` | `records search` |
| POST | `/bitable/v1/apps/{app_token}/tables/{table_id}/records` | `records create` |
| PUT | `/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}` | `records update` |
| DELETE | `/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}` | `records delete` |
| POST | `/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create` | `records batch-create` |
| POST | `/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_update` | `records batch-update` |
| POST | `/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete` | `records batch-delete` |

## 批量上限

所有 `batch_*` 接口单次最多 **500 条**。CLI 会自动分块。

## 限速错误码

| code | 含义 | CLI 行为 |
| --- | --- | --- |
| 99991400 | 频控 | 指数退避重试（1s → 2s → 4s，最多 3 次）|
| 1254607 | 多维表格频控 | 同上 |
| 99991663 / 99991668 | token 无效 | 清缓存重试一次 |
