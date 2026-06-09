"""交叉验证引擎——固定样本分层抽样、规则级 Cohen's Kappa 一致性"""
import copy
import json
import random
from auto_qc.domain.schemas import CrossValidationResult


def fixed_sample(
    results: list[dict],
    sample_size: int = 200,
    random_seed: int = 42,
) -> list[dict]:
    """
    固定样本量分层抽样。
    违规组和通过组各抽一半，保证两类都有代表。
    样本量自动封顶：不超过 total × 3/4，防止某组不够时另一组全抽光。
    """
    random.seed(random_seed)

    # 总量太小时直接全量返回
    if len(results) <= sample_size:
        return list(results)

    violation_items = [r for r in results if r.get("violations")]
    non_violation_items = [r for r in results if not r.get("violations")]

    cap = int(len(results) * 0.75)
    actual_size = min(sample_size, cap)

    # 两组尽量均分，但不超过各自的总数
    half = actual_size // 2
    v_sample = random.sample(violation_items, min(half, len(violation_items)))
    nv_remaining = actual_size - len(v_sample)
    nv_sample = random.sample(non_violation_items, min(nv_remaining, len(non_violation_items)))

    combined = v_sample + nv_sample
    random.shuffle(combined)
    return combined


def _cohen_kappa_for_rule(
    original_hit: set[str],
    recheck_hit: set[str],
    all_ids: list[str],
) -> dict:
    """
    对单条规则计算 Cohen's Kappa。

    混淆矩阵：
                 复检违规  复检通过
    原始违规        a        b
    原始通过        c        d

    Returns: {kappa, po, pe, agreement, total_judgments, tp, fp, fn, tn}
    """
    a = b = c = d = 0
    for item_id in all_ids:
        in_orig = item_id in original_hit
        in_recheck = item_id in recheck_hit
        if in_orig and in_recheck:
            a += 1
        elif in_orig and not in_recheck:
            b += 1
        elif not in_orig and in_recheck:
            c += 1
        else:
            d += 1

    total = a + b + c + d
    if total == 0:
        return {"kappa": 0.0, "po": 0.0, "pe": 0.0, "agreement": 0.0,
                "total_judgments": 0, "tp": 0, "fp": 0, "fn": 0, "tn": 0}

    po = (a + d) / total
    p_yes_orig = (a + b) / total
    p_yes_recheck = (a + c) / total
    p_no_orig = (c + d) / total
    p_no_recheck = (b + d) / total
    pe = p_yes_orig * p_yes_recheck + p_no_orig * p_no_recheck

    kappa = (po - pe) / (1 - pe) if pe < 1 else 0.0

    return {
        "kappa": round(kappa, 4),
        "po": round(po, 4),
        "pe": round(pe, 4),
        "agreement": round(po, 4),
        "total_judgments": total,
        "tp": a, "fp": c, "fn": b, "tn": d,
    }


def compare_results(
    original: list[dict],
    recheck: list[dict],
) -> CrossValidationResult:
    """
    规则级对比 + Cohen's Kappa。

    original / recheck 每条格式：
    {"id": "...", "violations": [{"rule_id": "R01", ...}, ...]}

    对每条规则分别计算：
    - Cohen's Kappa（排除随机一致）
    - 简单一致率
    - 混淆矩阵 (tp/fp/fn/tn)
    """
    # 建立每条对话的违规规则集合
    orig_map = {}
    for r in original:
        orig_map[r["id"]] = {v["rule_id"] for v in r.get("violations", [])}

    recheck_map = {}
    for r in recheck:
        recheck_map[r["id"]] = {v["rule_id"] for v in r.get("violations", [])}

    # 收集所有出现过的规则 ID
    all_rule_ids: set[str] = set()
    for rules in orig_map.values():
        all_rule_ids.update(rules)
    for rules in recheck_map.values():
        all_rule_ids.update(rules)

    # 两次都有的对话 ID 列表
    common_ids = [i for i in orig_map if i in recheck_map]

    per_rule = {}
    total_judgments = 0
    total_mismatches = 0

    for rule_id in sorted(all_rule_ids):
        orig_hit = {i for i in common_ids if rule_id in orig_map[i]}
        recheck_hit = {i for i in common_ids if rule_id in recheck_map[i]}

        stats = _cohen_kappa_for_rule(orig_hit, recheck_hit, common_ids)
        mismatches = stats["fp"] + stats["fn"]

        total_judgments += stats["total_judgments"]
        total_mismatches += mismatches

        per_rule[rule_id] = {
            "kappa": stats["kappa"],
            "agreement": stats["agreement"],
            "total_judgments": stats["total_judgments"],
            "mismatches": mismatches,
            "tp": stats["tp"], "fp": stats["fp"],
            "fn": stats["fn"], "tn": stats["tn"],
        }

    return CrossValidationResult.compute(total_mismatches, total_judgments, per_rule)


def _find_disputes(
    original: list[dict],
    recheck: list[dict],
    rule_id: str,
) -> list[str]:
    """找出指定规则下 original 和 recheck 判断不一致的对话 ID 列表。"""
    def _get_hit(entries: list[dict]) -> dict[str, bool]:
        result = {}
        for e in entries:
            result[e["id"]] = rule_id in {v["rule_id"] for v in e.get("violations", [])}
        return result

    orig_hit = _get_hit(original)
    rech_hit = _get_hit(recheck)
    disputes = []
    for cid in orig_hit:
        if cid in rech_hit and orig_hit[cid] != rech_hit[cid]:
            disputes.append(cid)
    return disputes


def _build_adjudication_prompt(
    disputes: list[str],
    rule_id: str,
    original: list[dict],
    recheck: list[dict],
    conv_text_map: dict[str, str],
) -> str:
    """构造裁决 prompt。对每条争议对话，列出前两次判断的结论和对话内容。"""
    rule_name = rule_id
    for e in original:
        for v in e.get("violations", []):
            if v["rule_id"] == rule_id:
                rule_name = v.get("rule_name", rule_id)
                break

    def _judgment(entries: list[dict], cid: str) -> str:
        for e in entries:
            if e["id"] == cid:
                if rule_id in {v["rule_id"] for v in e.get("violations", [])}:
                    return "违规"
                return "通过"
        return "通过"

    lines = [
        "你是一名质检裁决员。请对比以下对话的前两次质检判断，做出最终裁决。",
        "",
        f"## 争议规则: {rule_id} ({rule_name})",
        "",
        "对每条对话，请判断：该对话是否违反上述规则？",
        "",
    ]
    for i, cid in enumerate(disputes, 1):
        conv_text = conv_text_map.get(cid, "[对话内容不可用]")
        lines.extend([
            f"### 对话 {i}: {cid}",
            "",
            conv_text,
            "",
            f"第一次判断（原始质检）: {_judgment(original, cid)}",
            f"第二次判断（交叉复检）: {_judgment(recheck, cid)}",
            "",
        ])

    lines.extend([
        "## 输出格式",
        "",
        "```json",
        "{",
        '  "rulings": [',
        '    {"id": "对话ID", "violates": true, "reasoning": "简短推理"},',
        "    ...",
        "  ]",
        "}",
        "```",
    ])
    return "\n".join(lines)


async def adjudicate(
    original: list[dict],
    recheck: list[dict],
    qc_results: list[dict],
    rule_id: str,
    call_llm_fn,
    conv_text_map: dict[str, str],
) -> tuple[list[dict], float]:
    """对指定规则的争议对话执行第三次裁决。三局两胜，取 majority 作为最终结果。"""
    disputes = _find_disputes(original, recheck, rule_id)
    if not disputes:
        return qc_results, 0.0

    prompt = _build_adjudication_prompt(disputes, rule_id, original, recheck, conv_text_map)
    raw = await call_llm_fn(prompt)
    data = json.loads(raw)
    rulings = {r["id"]: r["violates"] for r in data.get("rulings", [])}

    def _check(entries: list[dict], cid: str) -> bool:
        for e in entries:
            if e["id"] == cid:
                return rule_id in {v["rule_id"] for v in e.get("violations", [])}
        return False

    # 收集违规样本的字段信息
    violated_samples = {}
    for e in original:
        for v in e.get("violations", []):
            if v["rule_id"] == rule_id:
                violated_samples[e["id"]] = v

    result = copy.deepcopy(qc_results)
    result_map = {r["id"]: r for r in result}

    for cid in disputes:
        orig_v = _check(original, cid)
        rech_v = _check(recheck, cid)
        adj_v = rulings.get(cid, orig_v)
        votes = [orig_v, rech_v, adj_v]
        majority = sum(votes) >= 2

        conv = result_map.get(cid)
        if conv is None:
            continue

        existing = [v for v in conv["violations"] if v["rule_id"] != rule_id]
        if majority:
            if cid in violated_samples:
                existing.append(dict(violated_samples[cid]))
            else:
                existing.append({
                    "rule_id": rule_id,
                    "rule_name": rule_id,
                    "severity": "中",
                    "evidence": "[裁决补充]",
                    "suggestion": "",
                })
            conv["violations"] = existing
            conv["status"] = "violation"
        else:
            conv["violations"] = existing
            if not existing:
                conv["status"] = "pass"

    return result, 0.0
