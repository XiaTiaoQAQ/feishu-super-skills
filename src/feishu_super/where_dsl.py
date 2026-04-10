"""Tiny `--where` DSL → Feishu `records/search` filter JSON.

Grammar (single-level conjunction, no nested groups beyond top-level):

    expr     := term ( ('and'|'or') term )*
    term     := field op value
    field    := bareword | "quoted string"
    op       := '=' | '!=' | '>' | '>=' | '<' | '<='
              | 'contains' | 'not_contains' | 'is_empty' | 'is_not_empty'
    value    := bareword | number | "double quoted" | 'single quoted'
              | (omitted for is_empty / is_not_empty)

The top-level conjunction must be homogeneous (all AND or all OR). For anything
more complex, fall back to `--filter '<raw_json>'`.

Example:
    name contains "abc" and status = active
    → {
        "conjunction": "and",
        "conditions": [
          {"field_name": "name", "operator": "contains", "value": ["abc"]},
          {"field_name": "status", "operator": "is", "value": ["active"]},
        ],
      }
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Map human op → Feishu operator
_OP_MAP: dict[str, str] = {
    "=": "is",
    "!=": "isNot",
    ">": "isGreater",
    ">=": "isGreaterEqual",
    "<": "isLess",
    "<=": "isLessEqual",
    "contains": "contains",
    "not_contains": "doesNotContain",
    "is_empty": "isEmpty",
    "is_not_empty": "isNotEmpty",
}

_UNARY_OPS = {"is_empty", "is_not_empty"}

_CONJ = {"and", "or", "AND", "OR", "And", "Or"}


class DslError(ValueError):
    pass


@dataclass
class _Token:
    kind: str  # 'word' | 'str' | 'op' | 'conj' | 'num'
    value: str


_TOKEN_RE = re.compile(
    r"""
    \s*
    (?:
        (?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
      | (?P<op>>=|<=|!=|=|>|<)
      | (?P<word>[\u4e00-\u9fffA-Za-z0-9_.\-/]+)
    )
    """,
    re.VERBOSE,
)


def _tokenize(s: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    while i < len(s):
        if s[i].isspace():
            i += 1
            continue
        m = _TOKEN_RE.match(s, i)
        if not m:
            raise DslError(f"DSL 解析失败：无法识别的字符 at pos {i}: {s[i:i+10]!r}")
        i = m.end()
        if m.group("str") is not None:
            raw = m.group("str")
            tokens.append(_Token("str", _unquote(raw)))
        elif m.group("op") is not None:
            tokens.append(_Token("op", m.group("op")))
        else:
            w = m.group("word")
            if w in _CONJ:
                tokens.append(_Token("conj", w.lower()))
            elif w in _OP_MAP:  # word-shaped operator e.g. contains
                tokens.append(_Token("op", w))
            else:
                tokens.append(_Token("word", w))
    return tokens


def _unquote(raw: str) -> str:
    # Strip surrounding quotes and handle only the minimal escape set we need:
    # \" \' \\ — everything else (including UTF-8 multibyte) passes through as-is.
    body = raw[1:-1]
    out: list[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in ('"', "'", "\\"):
                out.append(nxt)
                i += 2
                continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_where(expr: str) -> dict[str, Any]:
    """Parse a `--where` string into Feishu search filter JSON."""
    expr = expr.strip()
    if not expr:
        raise DslError("DSL 为空")
    tokens = _tokenize(expr)

    conditions: list[dict[str, Any]] = []
    conjunction: str | None = None

    i = 0
    n = len(tokens)
    while i < n:
        # parse one term: field op [value]
        if tokens[i].kind != "word" and tokens[i].kind != "str":
            raise DslError(f"期望字段名 at token {i}: {tokens[i]}")
        field_name = tokens[i].value
        i += 1
        if i >= n or tokens[i].kind != "op":
            raise DslError(f"字段 {field_name!r} 后期望操作符")
        op_raw = tokens[i].value
        if op_raw not in _OP_MAP:
            raise DslError(f"不支持的操作符 {op_raw!r}")
        op_feishu = _OP_MAP[op_raw]
        i += 1

        cond: dict[str, Any] = {"field_name": field_name, "operator": op_feishu}

        if op_raw in _UNARY_OPS:
            cond["value"] = []
        else:
            if i >= n or tokens[i].kind not in ("word", "str"):
                raise DslError(f"操作符 {op_raw!r} 后期望值")
            cond["value"] = [tokens[i].value]
            i += 1

        conditions.append(cond)

        if i >= n:
            break
        if tokens[i].kind != "conj":
            raise DslError(f"期望 and/or at token {i}: {tokens[i]}")
        this_conj = tokens[i].value
        if conjunction is None:
            conjunction = this_conj
        elif conjunction != this_conj:
            raise DslError(
                "本 DSL 不支持 and/or 混用；如需更复杂结构请用 --filter 传原生 JSON"
            )
        i += 1

    return {
        "conjunction": conjunction or "and",
        "conditions": conditions,
    }


def build_fuzzy_filter(query: str, text_field_names: list[str]) -> dict[str, Any] | None:
    """Build an OR-contains filter across all text-like fields."""
    if not query or not text_field_names:
        return None
    return {
        "conjunction": "or",
        "conditions": [
            {"field_name": name, "operator": "contains", "value": [query]}
            for name in text_field_names
        ],
    }


def parse_sort(spec: str) -> list[dict[str, Any]]:
    """Parse `field1 desc, field2 asc` → Feishu sort list."""
    out: list[dict[str, Any]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.rsplit(None, 1)
        if len(bits) == 2 and bits[1].lower() in ("asc", "desc"):
            out.append({"field_name": bits[0].strip(), "desc": bits[1].lower() == "desc"})
        else:
            out.append({"field_name": part, "desc": False})
    return out
