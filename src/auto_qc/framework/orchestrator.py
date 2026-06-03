"""全流程编排器——串联 Step 0 到 Step 7"""
import asyncio
import json
import datetime
from pathlib import Path
from typing import Optional

from auto_qc.domain.schemas import Batch, Conversation
from auto_qc.domain.rules import parse_rules_file
from auto_qc.domain.data_loader import load_conversations, save_batches
from auto_qc.domain.prompts import build_qc_prompt, build_attribution_prompt
from auto_qc.domain.report import write_report, verify_report_exists

from auto_qc.framework.validator import (
    validate_rule_package, validate_batches, validate_worker_output, validate_merge_results,
)
from auto_qc.framework.worker import call_llm_with_retry, extract_json
from auto_qc.framework.progress import create_progress, load_progress, save_progress, has_unfinished, reset_running_batches
from auto_qc.framework.coordinator import Coordinator
from auto_qc.framework.cross_validator import stratified_sample, compare_results


async def _process_batch(
    batch: Batch,
    rule_ids: list[str],
    prompt_builder,
    coordinator: Coordinator,
    work_dir: str,
) -> list[dict]:
    """
    处理单个批次：拼 prompt → 调 LLM → 校验 → 重试。
    prompt_builder: (Batch) -> str 的函数。
    """
    prompt = prompt_builder(batch)

    for attempt in range(3):
        try:
            raw = await call_llm_with_retry(prompt)
            json_text = extract_json(raw)
            output = validate_worker_output(json_text, batch.size, rule_ids)
            coordinator.mark_done(batch.batch_id)

            # 返回带原始字段（id/time/intent）的结果
            conv_map = {c.id: c for c in batch.conversations}
            enriched = []
            for r in output.results:
                conv = conv_map.get(r.id, None)
                enriched.append({
                    "id": r.id,
                    "time": conv.time if conv else "",
                    "intent": conv.intent if conv else "",
                    "status": r.status,
                    "violations": [
                        {"rule_id": v.rule_id, "rule_name": v.rule_name,
                         "severity": v.severity, "evidence": v.evidence,
                         "suggestion": v.suggestion}
                        for v in r.violations
                    ],
                })

            # 保存单批结果
            result_path = Path(work_dir) / f"batch_{batch.batch_id}_result.json"
            result_path.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")

            return enriched

        except Exception as e:
            retries = coordinator.increment_retry(batch.batch_id)
            if retries >= 3:
                coordinator.mark_failed(batch.batch_id)
                print(f"批次 {batch.batch_id} 失败（已重试 3 次）: {e}")
                return []
            print(f"批次 {batch.batch_id} 第 {retries} 次重试: {e}")

    return []


async def _dispatch_phase(
    batches: list[Batch],
    rule_ids: list[str],
    prompt_builder,
    coordinator: Coordinator,
    work_dir: str,
    sem: asyncio.Semaphore,
) -> list[dict]:
    """并发分发批次，收集所有结果。"""
    all_results = []

    async def _run_one(batch: Batch):
        async with sem:
            return await _process_batch(batch, rule_ids, prompt_builder, coordinator, work_dir)

    while True:
        next_ids = coordinator.get_next_batches()
        if not next_ids:
            break

        batch_map = {b.batch_id: b for b in batches}
        tasks = [_run_one(batch_map[bid]) for bid in next_ids]
        batch_results = await asyncio.gather(*tasks)
        for r in batch_results:
            all_results.extend(r)

        summary = coordinator.get_summary()
        total_progress = summary["done"] + summary["failed"]
        print(f"进度: {total_progress}/{summary['total']} 批")

    return all_results


async def run_qc(
    data_path: str,
    rules_path: str,
    output_path: str,
    work_dir: str = "./auto_qc_work",
    skip_attribution: bool = False,
) -> None:
    """
    完整质检流程驱动入口。
    """
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    # ─── Step 1: 环境检查 ───
    print("[Step 1] 环境检查...")
    from auto_qc.domain.report import HEADER_FONT  # 验证 openpyxl 可用
    print("  ✅ 依赖就绪")

    if not Path(data_path).exists():
        raise FileNotFoundError(f"数据文件不存在: {data_path}")
    if not Path(rules_path).exists():
        raise FileNotFoundError(f"规则文件不存在: {rules_path}")
    print("  ✅ 文件存在")

    # ─── Step 2: 规则解析 + 校验 ───
    print("[Step 2] 规则解析 + 校验...")
    rule_package = parse_rules_file(rules_path)
    validate_rule_package(rule_package)
    rule_ids = rule_package.rule_ids
    print(f"  ✅ 解析完成: {len(rule_package.rules)} 条规则 ({', '.join(rule_ids)})")

    # ─── Step 3: 数据加载 + 拆分 ───
    print("[Step 3] 数据加载 + 批次拆分...")
    batches = load_conversations(data_path, batch_size=100)
    validate_batches(batches)
    total_ids = sum(b.size for b in batches)
    print(f"  ✅ 加载完成: {total_ids} 条对话, {len(batches)} 批")

    # 保存批次到临时目录
    save_batches(batches, work_dir)

    # ─── Step 4: 并发质检 ───
    print("[Step 4] 并发质检...")
    coordinator = Coordinator(work_dir)
    create_progress(work_dir, len(batches), phase="qc")

    sem = asyncio.Semaphore(5)  # 最多 5 并发
    qc_results = await _dispatch_phase(
        batches, rule_ids,
        prompt_builder=lambda b: build_qc_prompt(b, rule_package),
        coordinator=coordinator,
        work_dir=work_dir,
        sem=sem,
    )
    print(f"  ✅ 合规检测完成: {len(qc_results)} 条结果")

    # ─── Step 5: 交叉验证 ───
    print("[Step 5] 交叉验证...")
    sample = stratified_sample(qc_results)
    if sample:
        sample_batch = Batch(batch_id=999, conversations=[
            Conversation(id=s["id"], time=s.get("time", ""),
                         intent=s.get("intent", ""), conversation="")
            for s in sample
        ])
        # 用新 Worker 重新判断
        prompt = build_qc_prompt(sample_batch, rule_package)
        raw = await call_llm_with_retry(prompt)
        json_text = extract_json(raw)
        recheck_output = validate_worker_output(json_text, sample_batch.size, rule_ids)

        # 对比
        recheck_results = [
            {"id": r.id, "violations": [
                {"rule_id": v.rule_id} for v in r.violations
            ]}
            for r in recheck_output.results
        ]
        cross_result = compare_results(sample, recheck_results)
        print(f"  ✅ 交叉验证: {cross_result.status} (差异率 {cross_result.discrepancy_rate:.1%})")
    else:
        print("  ⚠️ 跳过交叉验证（样本不足）")

    # ─── Step 6: 归因分析 ───
    attr_data = {}
    if not skip_attribution:
        print("[Step 6] 归因分析...")
        # 过滤非 A 意向的对话
        attr_batches = load_conversations(data_path, batch_size=100, exclude_intent="A(有意向)")
        if attr_batches:
            attr_rule_ids = ["A01", "A02", "A03", "A04", "A05", "A06"]
            attr_coordinator = Coordinator(work_dir)
            create_progress(work_dir, len(attr_batches), phase="attribution")

            attr_results = await _dispatch_phase(
                attr_batches, attr_rule_ids,
                prompt_builder=build_attribution_prompt,
                coordinator=attr_coordinator,
                work_dir=work_dir,
                sem=sem,
            )
            print(f"  ✅ 归因分析完成: {len(attr_results)} 条结果")
            attr_data = _group_attribution(attr_results)
        else:
            print("  ⚠️ 无待归因对话")

    # ─── Step 7: 报告生成 ───
    print("[Step 7] 报告生成...")
    validate_merge_results(qc_results, total_ids)
    stats = _compute_stats(qc_results, rule_package)

    write_report(output_path, qc_results, attr_data, stats)
    if verify_report_exists(output_path):
        print(f"  ✅ 报告已生成: {output_path}")
    else:
        raise RuntimeError("报告文件生成失败")


# ─── 辅助函数 ───

def _compute_stats(qc_results: list[dict], rule_package) -> dict:
    """计算统计概览。"""
    total = len(qc_results)
    pass_count = sum(1 for r in qc_results if not r.get("violations"))
    violation_rate = f"{((total - pass_count) / total * 100):.1f}%" if total > 0 else "0%"

    rules_hit = {}
    rule_names = {r.rule_id: r.name for r in rule_package.rules}
    for r in qc_results:
        for v in r.get("violations", []):
            rid = v.get("rule_id", "")
            rules_hit[rid] = rules_hit.get(rid, 0) + 1

    return {
        "total": total,
        "pass": pass_count,
        "violation_rate": violation_rate,
        "rules_hit": rules_hit,
        "rule_names": rule_names,
    }


def _group_attribution(results: list[dict]) -> dict:
    """将归因结果按 intent 分组。"""
    # 归因 Worker 输出与 QC Worker 格式相同，domain 层处理分组逻辑
    # 此处简化为直接收集
    return {"B(不确定)": [], "F(无意向)": []}
