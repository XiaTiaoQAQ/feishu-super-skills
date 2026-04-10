"""Feishu Bitable field type code → human name.

Source: dongjing-manager/app/dongjing-manager-backend/src/sync/bitable-sync/sync-helpers.ts
and the Feishu open platform docs. Codes are stable.
"""

from __future__ import annotations

FIELD_TYPE_NAMES: dict[int, str] = {
    1: "Text",              # 多行文本
    2: "Number",            # 数字
    3: "SingleSelect",      # 单选
    4: "MultiSelect",       # 多选
    5: "DateTime",          # 日期（毫秒时间戳）
    7: "Checkbox",          # 复选框
    11: "User",             # 人员
    13: "Phone",            # 电话号码
    15: "Url",              # 超链接
    17: "Attachment",       # 附件
    18: "SingleLink",       # 单向关联
    19: "Lookup",           # 查找引用
    20: "Formula",          # 公式
    21: "DuplexLink",       # 双向关联
    22: "Location",         # 地理位置
    23: "Group",            # 群聊
    1001: "CreatedTime",    # 创建时间（自动）
    1002: "ModifiedTime",   # 最后更新时间（自动）
    1003: "CreatedUser",    # 创建人（自动）
    1004: "ModifiedUser",   # 修改人（自动）
    1005: "AutoNumber",     # 自动编号
}

# Field types considered "text-like" for fuzzy search fan-out.
TEXT_LIKE_TYPES: frozenset[int] = frozenset({1, 13, 15})

# Field types that are read-only / cannot be written by the user.
READ_ONLY_TYPES: frozenset[int] = frozenset(
    {19, 20, 1001, 1002, 1003, 1004, 1005}
)


def type_name(code: int) -> str:
    return FIELD_TYPE_NAMES.get(code, f"Unknown({code})")


def is_text_like(code: int) -> bool:
    return code in TEXT_LIKE_TYPES


def is_read_only(code: int) -> bool:
    return code in READ_ONLY_TYPES
