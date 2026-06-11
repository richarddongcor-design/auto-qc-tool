"""通用契约校验——每个步骤的输入输出校验"""
import json
from typing import Optional
from auto_qc.domain.schemas import WorkerOutput, RulePackage, Batch


class ValidationError(Exception):
    """校验失败异常。"""
    pass


# ─── 规则校验 ───

def validate_rule_package(pkg: RulePackage) -> None:
    """校验规则包：ID 唯一、severity 合法、必填字段完整。"""
    from auto_qc.domain.rules import validate_rule_package as _validate
    errors = _validate(pkg)
    if errors:
        raise ValidationError("规则校验失败:\n" + "\n".join(f"  - {e}" for e in errors))


# ─── 数据校验 ───

def validate_batches(batches: list[Batch]) -> None:
    """校验加载后的批次数据。"""
    if not batches:
        raise ValidationError("批次列表为空，未加载到任何数据")

    all_ids = set()
    for batch in batches:
        if batch.size == 0:
            raise ValidationError(f"批次 {batch.batch_id} 为空")
        for c in batch.conversations:
            if not c.id:
                raise ValidationError(f"批次 {batch.batch_id} 存在空 ID")
            if c.id in all_ids:
                raise ValidationError(f"对话 ID 重复: {c.id}")
            all_ids.add(c.id)

    if len(all_ids) == 0:
        raise ValidationError("未读取到任何有效对话")


# ─── Worker 结果校验 ───

def validate_worker_output(raw_json: str, batch_size: int, expected_rule_ids: list[str]) -> WorkerOutput:
    """
    校验 Worker 返回的 JSON：
    1. JSON 合法
    2. 结果数量 == 批次大小
    3. 每条有 id 且不重复
    4. rules_checked 包含所有规则 ID
    5. spot_check_details >= 3 条
    6. 每条 violation 必填字段完整
    """
    # Step 1: JSON 解析
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise ValidationError(f"Worker 返回无效 JSON: {e}")

    # Step 2: 解析为 WorkerOutput
    try:
        output = WorkerOutput.from_dict(data)
    except Exception as e:
        raise ValidationError(f"Worker 结果结构解析失败: {e}")

    # Step 3: 结果数量
    actual_count = len(output.results)
    if actual_count != batch_size:
        raise ValidationError(
            f"结果数量不匹配: 期望 {batch_size} 条，实际 {actual_count} 条 (batch {output.batch_id})"
        )

    # Step 4: ID 唯一且无空
    seen_ids = set()
    for r in output.results:
        if not r.id:
            raise ValidationError(f"批次 {output.batch_id} 存在空 ID 的结果")
        if r.id in seen_ids:
            raise ValidationError(f"批次 {output.batch_id} 存在重复 ID: {r.id}")
        seen_ids.add(r.id)

    # Step 5: rules_checked 完整性
    checked = set(output.rules_checked)
    missing = set(expected_rule_ids) - checked
    if missing:
        raise ValidationError(
            f"批次 {output.batch_id} 未检查的规则: {missing}"
        )

    # Step 6: spot_check_details 不少于 3 条
    if len(output.spot_check_details) < 3:
        raise ValidationError(
            f"批次 {output.batch_id} spot_check_details 仅 {len(output.spot_check_details)} 条，至少需要 3 条"
        )

    # Step 7: 每条 violation 必填字段完整
    for r in output.results:
        if r.status == "violation":
            for v in r.violations:
                if not v.rule_id:
                    raise ValidationError(f"ID {r.id}: violation 缺少 rule_id")
                if not v.evidence:
                    raise ValidationError(f"ID {r.id} {v.rule_id}: violation 缺少 evidence")
                if not v.suggestion:
                    raise ValidationError(f"ID {r.id} {v.rule_id}: violation 缺少 suggestion")

    return output


def validate_merge_results(
    results: list[dict], expected_total: int
) -> None:
    """校验合并后的结果总数。"""
    if len(results) != expected_total:
        raise ValidationError(
            f"合并结果总数不匹配: 期望 {expected_total} 条，实际 {len(results)} 条"
        )


def validate_single_rule_output(
    raw_json: str,
    batch_size: int,
    expected_rule_id: str,
    expected_conv_ids: set[str],
) -> dict:
    """
    校验单规则 LLM 调用的返回结果。

    校验项：
    1. JSON 合法
    2. rule_id 匹配
    3. results 数量 == batch_size
    4. 每条结果有 id 且属于 expected_conv_ids
    5. 每条结果有 violates (true/false)
    6. violates=true 时 evidence 和 reasoning 非空

    所有校验项失败均触发整批重试（抛出 ValidationError）。
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise ValidationError(f"单规则输出 JSON 解析失败: {e}")

    actual_rule_id = data.get("rule_id", "")
    if actual_rule_id != expected_rule_id:
        raise ValidationError(
            f"rule_id 不匹配: 期望 {expected_rule_id}，实际 {actual_rule_id}"
        )

    batch_id = data.get("batch_id")
    if not isinstance(batch_id, int):
        raise ValidationError("batch_id 字段缺失或不是整数")

    results = data.get("results")
    if not isinstance(results, list):
        raise ValidationError("results 字段缺失或不是数组")
    if len(results) != batch_size:
        raise ValidationError(
            f"结果数量不匹配: 期望 {batch_size} 条，实际 {len(results)} 条"
        )

    seen_ids = set()
    for r in results:
        rid = r.get("id", "")
        if not rid:
            raise ValidationError("存在空 ID 的结果")
        if rid in seen_ids:
            raise ValidationError(f"存在重复 ID: {rid}")
        if rid not in expected_conv_ids:
            raise ValidationError(f"ID {rid} 不在期望的对话 ID 集合中")
        seen_ids.add(rid)

        violates = r.get("violates")
        if violates not in (True, False):
            raise ValidationError(f"ID {rid}: violates 值无效 ({violates})")

        if violates:
            if not r.get("evidence", "").strip():
                raise ValidationError(f"ID {rid}: violates=true 但 evidence 为空")
            if not r.get("reasoning", "").strip():
                raise ValidationError(f"ID {rid}: violates=true 但 reasoning 为空")

    return data
