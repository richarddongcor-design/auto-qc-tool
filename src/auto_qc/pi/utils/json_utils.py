"""JSON 提取和校验工具，用于处理 Agent 输出。"""

import json
import re
from typing import Any


def extract_json_from_text(text: str) -> Any:
    """从 Agent 输出中提取 JSON。

    处理以下情况：
    - 纯 JSON
    - Markdown 包裹的 JSON (```json ... ```)
    - 带前后文字说明的 JSON
    """
    # 尝试直接解析
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试从 markdown 代码块中提取（允许 ```json 后无换行的情况）
    pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    match = re.search(pattern, text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试找第一个 JSON 数组或对象
    # 找数组
    bracket_count = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "[":
            if bracket_count == 0:
                start = i
            bracket_count += 1
        elif ch == "]":
            bracket_count -= 1
            if bracket_count == 0 and start is not None:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    pass

    # 找对象（包括从对象包裹中提取数组，处理 {"patterns": [...]} 包裹）
    brace_count = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if brace_count == 0:
                start = i
            brace_count += 1
        elif ch == "}":
            brace_count -= 1
            if brace_count == 0 and start is not None:
                try:
                    obj = json.loads(text[start : i + 1])
                    # 如果是 dict，优先返回内部第一个非空数组（处理包裹格式）
                    if isinstance(obj, dict):
                        for v in obj.values():
                            if isinstance(v, list) and len(v) > 0:
                                return v
                    return obj
                except json.JSONDecodeError:
                    pass

    # 最后尝试 json_repair 库
    try:
        from json_repair import repair_json
        repaired = repair_json(text)
        return json.loads(repaired)
    except Exception:
        pass

    preview = text[:500] + ("..." if len(text) > 500 else "")
    raise ValueError(f"无法从输出中提取合法 JSON:\n{preview}")


def heal_json(text: str) -> Any | None:
    """自修复常见 JSON 格式错误后尝试解析。

    处理：
    - 尾逗号（trailing comma）
    - 单引号代替双引号
    - 键名缺少引号（如 {key: "value"} → {"key": "value"}）
    - 文字被截断（尝试补全不完整 JSON 后解析）

    Returns:
        解析后的 Python 对象，或 None（如果所有修复策略都失败）
    """
    # 1. 修复尾逗号（数组或对象最后一个元素后面的逗号）
    text_clean = re.sub(r",\s*([}\]])", r"\1", text)
    if text_clean != text:
        try:
            return extract_json_from_text(text_clean)
        except (json.JSONDecodeError, ValueError):
            pass

    # 2. 修复单引号：将属性名和字符串值的单引号替换为双引号
    try:
        single_quote_fixed = re.sub(r"(?<!\\)'", '"', text)
        return extract_json_from_text(single_quote_fixed)
    except (json.JSONDecodeError, ValueError):
        pass

    # 3. 修复未加引号的键名（中文字段名）
    try:
        unquoted_keys = re.sub(
            r'(?<=[{,])\s*([a-zA-Z_一-鿿][a-zA-Z0-9_一-鿿]*)\s*(?=\s*:)',
            r'"\1"',
            text,
        )
        return extract_json_from_text(unquoted_keys)
    except (json.JSONDecodeError, ValueError):
        pass

    # 4. 最后尝试 json_repair 库
    try:
        from json_repair import repair_json
        repaired = repair_json(text)
        return extract_json_from_text(repaired)
    except Exception:
        pass

    return None