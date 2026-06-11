"""全流程编排器——Step 1 到 Step 6"""
import asyncio
import json
from pathlib import Path

from auto_qc.domain.rules import load_rule_sets, validate_rule_sets
from auto_qc.domain.data_loader import load_conversations, save_batches
from auto_qc.domain.prompts import build_single_rule_prompt
from auto_qc.domain.report import write_report, verify_report_exists
from auto_qc.domain.merger import merge_to_wide_rows

from auto_qc.framework.validator import (
    validate_batches,
    validate_single_rule_output,
)
from auto_qc.framework.worker import call_llm_with_retry, extract_json, reset_token_stats, get_token_stats
from auto_qc.framework.progress import create_progress, load_progress, save_progress
from auto_qc.framework.coordinator import Coordinator


async def run_qc(
    data_path: str,
    rule_set_names: list[str],
    output_path: str,
    work_dir: str = "./auto_qc_work",
) -> None:
    """
    完整质检流程驱动入口（v2.0 逐规则打标模式）。
    """
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    reset_token_stats()

    # ─── Step 1: 环境检查 ───
    print("[Step 1] 环境检查...")
    from auto_qc.domain.report import HEADER_FONT
    print("  [OK] 依赖就绪")
    if not Path(data_path).exists():
        raise FileNotFoundError(f"数据文件不存在: {data_path}")
    print("  [OK] 文件存在")

    # ─── Step 2: 规则集加载 ───
    print("[Step 2] 规则集加载...")
    rule_sets = load_rule_sets(rule_set_names)
    all_rules = []
    rule_name_map = {}
    for rs in rule_sets:
        for r in rs.rules:
            all_rules.append(r)
            rule_name_map[r.rule_id] = r.name
    print(f"  [OK] 加载 {len(rule_sets)} 个规则集, {len(all_rules)} 条规则")

    errors = validate_rule_sets(rule_sets)
    if errors:
        raise ValueError(f"规则集校验失败:\n" + "\n".join(f"  - {e}" for e in errors))

    # ─── Step 3: 数据加载 + 批次拆分 ───
    print("[Step 3] 数据加载 + 批次拆分...")
    batches = load_conversations(data_path, batch_size=25)
    total_ids = sum(b.size for b in batches)
    validate_batches(batches)
    print(f"  [OK] 加载完成: {total_ids} 条对话, {len(batches)} 批")

    all_convs = []
    for b in batches:
        for c in b.conversations:
            all_convs.append({"id": c.id, "time": c.time, "intent": c.intent})

    save_batches(batches, work_dir)

    # ─── Step 4: 逐规则并发打标 ───
    print("[Step 4] 逐规则并发打标...")
    coordinator = Coordinator(work_dir)
    create_progress(work_dir, len(batches), phase="qc")

    batch_conv_ids = {b.batch_id: set(b.ids) for b in batches}
    sem = asyncio.Semaphore(50)

    per_rule_results: dict[str, dict[str, dict]] = {}

    async def _run_single_rule(
        rule,
        batches,
    ) -> dict[str, dict[str, dict]]:
        rule_conv_results = {}

        async def _check_batch(batch):
            async with sem:
                for attempt in range(3):
                    try:
                        prompt = build_single_rule_prompt(batch, rule)
                        raw = await call_llm_with_retry(prompt)
                        json_text = extract_json(raw)
                        data = validate_single_rule_output(
                            json_text, batch.size, rule.rule_id, batch_conv_ids[batch.batch_id],
                        )
                        coordinator.mark_done(batch.batch_id)
                        return data["results"]
                    except Exception as e:
                        if attempt < 2:
                            print(f"  {rule.rule_id} 批次 {batch.batch_id} 第 {attempt+1} 次重试: {e}")
                        else:
                            print(f"  {rule.rule_id} 批次 {batch.batch_id} 失败（重试耗尽）: {e}")
                            return [{"id": cid, "violates": False, "evidence": "", "reasoning": ""}
                                    for cid in batch_conv_ids[batch.batch_id]]

        tasks = [_check_batch(b) for b in batches]
        all_batch_results = await asyncio.gather(*tasks)

        for batch_results in all_batch_results:
            for r in batch_results:
                rule_conv_results[r["id"]] = {
                    "violates": r.get("violates", False),
                    "evidence": r.get("evidence", ""),
                }
        return rule_conv_results

    # 对所有规则并发执行
    rule_tasks = [_run_single_rule(r, batches) for r in all_rules]
    all_results_list = await asyncio.gather(*rule_tasks)

    for i, rule in enumerate(all_rules):
        per_rule_results[rule.rule_id] = all_results_list[i]

    wide_rows = merge_to_wide_rows(per_rule_results, all_convs, rule_name_map)
    if len(wide_rows) != total_ids:
        raise RuntimeError(f"合并结果数 {len(wide_rows)} 与预期 {total_ids} 不匹配")
    print(f"  [OK] 逐规则打标完成: {len(all_rules)} 条规则 × {len(batches)} 批 = {len(all_rules) * len(batches)} 次调用")

    # ─── Step 5: 统计计算 ───
    print("[Step 5] 统计计算...")
    stats = _compute_stats_v2(wide_rows, rule_sets, rule_name_map)
    print(f"  [OK] 总体违规率: {stats['violation_rate']}")

    # ─── Step 6: 报告生成 ───
    print("[Step 6] 报告生成...")
    write_report(output_path, wide_rows, stats)
    if verify_report_exists(output_path):
        print(f"  [OK] 报告已生成: {output_path}")
    else:
        raise RuntimeError("报告文件生成失败")

    # Token 消耗
    token_summary = get_token_stats().summary()
    print(f"\n  [数据] Token 消耗: 输入 {token_summary['total_input_tokens']:,} | 输出 {token_summary['total_output_tokens']:,} | 总计 {token_summary['total_tokens']:,}")

    summary_data = {
        "data_file": data_path,
        "rule_sets": rule_set_names,
        "total_conversations": total_ids,
        "total_checks": len(all_rules) * total_ids,
        "violation_rate": stats["violation_rate"],
        "token_usage": token_summary,
    }
    summary_path = Path(work_dir) / "summary.json"
    summary_path.write_text(json.dumps(summary_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [OK] 运行摘要已保存: {summary_path}")


# ─── 辅助函数 ───

def _compute_stats_v2(wide_rows: list[dict], rule_sets: list, rule_name_map: dict) -> dict:
    """v2.0 统计概览计算。"""
    total = len(wide_rows)
    violation_rows = [r for r in wide_rows if any(rr.get("result") == "违规" for rr in r["rules"].values())]
    pass_rows = [r for r in wide_rows if r not in violation_rows]

    # 规则集统计
    rule_set_stats = {}
    for rs in rule_sets:
        rs_rules = [r.rule_id for r in rs.rules]
        total_checks = total * len(rs_rules)
        violations = sum(
            1 for row in wide_rows
            for rid in rs_rules
            if row["rules"].get(rid, {}).get("result") == "违规"
        )
        rule_set_stats[rs.name] = {
            "total_checks": total_checks,
            "violations": violations,
            "rate": f"{violations / total_checks * 100:.1f}%" if total_checks else "0%",
        }

    # 规则统计
    rule_stats = {}
    for row in wide_rows:
        for rid, rr in row["rules"].items():
            if rid not in rule_stats:
                rule_stats[rid] = {"pass": 0, "violation": 0}
            if rr["result"] == "违规":
                rule_stats[rid]["violation"] += 1
            else:
                rule_stats[rid]["pass"] += 1
    for rid, s in rule_stats.items():
        s["pass_rate"] = f"{s['pass'] / total * 100:.1f}%"
        s["violation_rate"] = f"{s['violation'] / total * 100:.1f}%"

    # 问题分布
    problem_distribution = []
    for rid, s in rule_stats.items():
        if s["violation"] > 0:
            problem_distribution.append({
                "rule_id": rid,
                "rule_name": rule_name_map.get(rid, rid),
                "count": s["violation"],
                "ratio": f"{s['violation'] / len(violation_rows) * 100:.1f}%" if violation_rows else "0%",
            })
    problem_distribution.sort(key=lambda x: x["count"], reverse=True)

    return {
        "total": total,
        "pass_count": len(pass_rows),
        "violation_count": len(violation_rows),
        "violation_rate": f"{len(violation_rows) / total * 100:.1f}%" if total else "0%",
        "rule_set_stats": rule_set_stats,
        "rule_stats": rule_stats,
        "problem_distribution": problem_distribution,
        "rule_name_map": rule_name_map,
    }
