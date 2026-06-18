"""LLM 输出校验层。

提供各 Phase 的 JSON schema 校验 + 内容合理性检查。
校验失败时返回明确错误信息，供重试机制反馈给 LLM。
"""
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# 各 Phase 的校验函数注册表
_VALIDATORS: dict[str, Callable] = {}


def register_validator(phase: str):
    """装饰器：注册 Phase 的校验函数。"""
    def decorator(func):
        _VALIDATORS[phase] = func
        return func
    return decorator


def validate_output(phase: str, data: Any) -> tuple[bool, str]:
    """校验 LLM 输出数据。

    Args:
        phase: Phase 名称（explorer, aggregator, dedup_merge, verifier）
        data: 解析后的 Python 对象（通常是 dict 或 list）

    Returns:
        (valid, error_message) — valid 为 True 时 error_message 为空
    """
    validator = _VALIDATORS.get(phase)
    if not validator:
        return True, ""  # 没有注册校验器，默认通过
    return validator(data)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Phase 1: 数据预处理校验
# ---------------------------------------------------------------------------

@register_validator("phase1")
def _validate_phase1(data: Any) -> tuple[bool, str]:
    """校验 Phase 1 chunk 文件。"""
    import json
    from pathlib import Path
    chunk_dir = Path(data) if not isinstance(data, Path) else data
    chunk_files = sorted(chunk_dir.glob("chunk_*.jsonl"))
    if not chunk_files:
        return False, "没有生成任何 chunk 文件"
    for cf in chunk_files:
        lines = cf.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return False, f"{cf.name} 文件为空"
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                return False, f"{cf.name} 第 {i+1} 行 JSON 解析失败: {e}"
            if "id" not in obj or not str(obj.get("id", "")).strip():
                return False, f"{cf.name} 第 {i+1} 行缺少 id 或 id 为空"
            if "turns" not in obj or not isinstance(obj.get("turns"), list):
                return False, f"{cf.name} 第 {i+1} 行缺少 turns 或 turns 不是数组"

    return True, ""

# Phase 2: Explorer（探索发现）校验
# ---------------------------------------------------------------------------

@register_validator("explorer")
def _validate_explorer(data: Any) -> tuple[bool, str]:
    """校验 Phase 2 探索发现输出。

    期望格式: list[dict] — 每个 dict 是一个发现的 pattern。
    """
    if not isinstance(data, list):
        return False, f"输出必须是 JSON 数组，当前类型: {type(data).__name__}"
    if len(data) == 0:
        return False, "输出数组不能为空（至少发现 1 个 pattern）"

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            return False, f"第 {i} 个元素不是字典"

        # 必需字段检查
        required = ["pattern_name", "description", "severity", "found_count", "total_checked", "examples"]
        for field in required:
            if field not in item:
                return False, f"第 {i} 个 pattern 缺少字段: {field}"

        # 类型检查
        if not isinstance(item["pattern_name"], str) or not item["pattern_name"].strip():
            return False, f"第 {i} 个 pattern_name 必须是非空字符串"
        if not isinstance(item["description"], str) or not item["description"].strip():
            return False, f"第 {i} 个 description 必须是非空字符串"

        # severity 枚举检查
        if item["severity"] not in ("high", "medium", "low"):
            return False, f"第 {i} 个 severity 必须是 high/medium/low，当前值: {item['severity']}"

        # 数值合理性检查
        if not isinstance(item["found_count"], (int, float)) or item["found_count"] < 0:
            return False, f"第 {i} 个 found_count 必须是非负整数"
        if not isinstance(item["total_checked"], (int, float)) or item["total_checked"] < 0:
            return False, f"第 {i} 个 total_checked 必须是非负整数"
        if item["found_count"] > item["total_checked"]:
            return False, f"第 {i} 个 found_count ({item['found_count']}) 不能大于 total_checked ({item['total_checked']})"

        # examples 数组检查
        if not isinstance(item["examples"], list) or len(item["examples"]) < 2:
            return False, f"第 {i} 个 pattern 的 examples 数组至少需要 2 个元素，当前: {len(item.get('examples', []))}"

        for j, ex in enumerate(item["examples"]):
            if not isinstance(ex, dict):
                return False, f"第 {i} 个 pattern 的 examples[{j}] 不是字典"
            if "dialogue_id" not in ex or not str(ex["dialogue_id"]).strip():
                return False, f"第 {i} 个 pattern 的 examples[{j}] 缺少 dialogue_id"
            if "quote" not in ex or not str(ex["quote"]).strip():
                return False, f"第 {i} 个 pattern 的 examples[{j}] 缺少 quote"

    return True, ""


# ---------------------------------------------------------------------------
# Phase 3: Topic Clustering（主题聚类）校验
# ---------------------------------------------------------------------------

@register_validator("topic_clustering")
def _validate_topic_clustering(data: Any) -> tuple[bool, str]:
    """校验 Phase 3 主题聚类输出。

    期望格式: list[dict] — 每个 dict 是一个主题组。
    """
    if not isinstance(data, list):
        return False, f"输出必须是 JSON 数组，当前类型: {type(data).__name__}"
    if len(data) == 0:
        return False, "输出数组不能为空"

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            return False, f"第 {i} 个元素不是字典"

        required = ["topic_name", "pattern_ids", "pattern_names"]
        for field in required:
            if field not in item:
                return False, f"第 {i} 个主题组缺少字段: {field}"

        if not isinstance(item["topic_name"], str) or not item["topic_name"].strip():
            return False, f"第 {i} 个 topic_name 必须是非空字符串"

        if not isinstance(item["pattern_ids"], list) or len(item["pattern_ids"]) == 0:
            return False, f"第 {i} 个 pattern_ids 必须是非空数组"

        if not isinstance(item["pattern_names"], list) or len(item["pattern_names"]) == 0:
            return False, f"第 {i} 个 pattern_names 必须是非空数组"

    return True, ""


# ---------------------------------------------------------------------------
# Phase 5/6: Aggregator（规则聚合）校验
# ---------------------------------------------------------------------------

def _validate_rules_common(data: Any, phase_name: str) -> tuple[bool, str]:
    """规则校验通用函数（Phase 5 和 Phase 6 共用）。"""
    if not isinstance(data, list):
        return False, f"输出必须是 JSON 数组，当前类型: {type(data).__name__}"
    if len(data) == 0:
        return False, "输出数组不能为空"

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            return False, f"第 {i} 个元素不是字典"

        required = ["rule_id", "rule_name", "description", "detection_logic", "depth", "confidence", "support_count"]
        for field in required:
            if field not in item:
                return False, f"第 {i} 个规则缺少字段: {field}"

        if not isinstance(item["rule_name"], str) or not item["rule_name"].strip():
            return False, f"第 {i} 个 rule_name 必须是非空字符串"
        if not isinstance(item["description"], str) or not item["description"].strip():
            return False, f"第 {i} 个 description 必须是非空字符串"
        if not isinstance(item["detection_logic"], str) or not item["detection_logic"].strip():
            return False, f"第 {i} 个 detection_logic 必须是非空字符串"

        if item["confidence"] not in ("high", "medium", "low"):
            return False, f"第 {i} 个 confidence 必须是 high/medium/low，当前: {item['confidence']}"

        if item.get("depth", "") not in ("superficial", "moderate", "deep"):
            return False, f"第 {i} 个 depth 必须是 superficial/moderate/deep，当前: {item.get('depth', '')}"

        if not isinstance(item["support_count"], (int, float)) or item["support_count"] <= 0:
            return False, f"第 {i} 个 support_count 必须是正整数"

    return True, ""


@register_validator("aggregator")
def _validate_aggregator(data: Any) -> tuple[bool, str]:
    return _validate_rules_common(data, "aggregator")


# ---------------------------------------------------------------------------
# Phase 5: Verifier（规则验证）校验
# ---------------------------------------------------------------------------

@register_validator("verifier")
def _validate_verifier(data: Any) -> tuple[bool, str]:
    """校验 Phase 6 规则验证输出。

    期望格式: list[dict] — 每条规则的验证结果。
    """
    if not isinstance(data, list):
        return False, f"输出必须是 JSON 数组，当前类型: {type(data).__name__}"
    if len(data) == 0:
        return False, "输出数组不能为空"

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            return False, f"第 {i} 个元素不是字典"

        required = ["rule_id", "rule_name", "total_checked", "hit_count", "hit_rate", "status"]
        for field in required:
            if field not in item:
                return False, f"第 {i} 个验证结果缺少字段: {field}"

        if not isinstance(item["total_checked"], (int, float)) or item["total_checked"] <= 0:
            return False, f"第 {i} 个 total_checked 必须是正整数"

        if not isinstance(item["hit_count"], (int, float)) or item["hit_count"] < 0:
            return False, f"第 {i} 个 hit_count 必须是非负整数"
        if item["hit_count"] > item["total_checked"]:
            return False, f"第 {i} 个 hit_count ({item['hit_count']}) 不能大于 total_checked ({item['total_checked']})"

        if not isinstance(item["hit_rate"], (int, float)) or not (0 <= item["hit_rate"] <= 1):
            return False, f"第 {i} 个 hit_rate 必须在 0-1 之间，当前: {item['hit_rate']}"

        if item["status"] not in ("valid", "too_narrow"):
            return False, f"第 {i} 个 status 必须是 valid/too_narrow，当前: {item['status']}"

    return True, ""

# ---------------------------------------------------------------------------
# Phase 6: 规则输出校验
# ---------------------------------------------------------------------------

@register_validator("phase6_output")
def _validate_phase6_output(data: Any) -> tuple[bool, str]:
    """校验 Phase 6 输出 rules.md 文件。"""
    from pathlib import Path
    out_dir = Path(data) if not isinstance(data, Path) else data
    rules_file = out_dir / "rules.md"
    if not rules_file.exists():
        return False, "rules.md 文件不存在"
    content = rules_file.read_text(encoding="utf-8").strip()
    if not content:
        return False, "rules.md 文件为空"
    if "## RULE-" not in content:
        return False, "rules.md 中没有质检规则条目"
    return True, ""
