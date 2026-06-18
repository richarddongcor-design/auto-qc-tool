"""Pipeline 状态机引擎。

6 阶段自动流转：Phase 1 → 6，用户只需运行一次 `python -m auto_qc.pi.engine.pipeline`。
每个阶段有前置校验、后置校验、失败重试。
"""

import argparse
import json
import logging
import random
import sys
from datetime import datetime
from pathlib import Path

from auto_qc.pi.agents.config import HarnessConfig
from auto_qc.pi.core.domain_loader import load_domain
from auto_qc.pi.utils.excel_parser import split_into_chunks, generate_phase1_report
from auto_qc.pi.utils.report_generator import generate_rules_md, generate_rules_summary
from auto_qc.pi.engine.scheduler import Scheduler
from auto_qc.pi.engine.tracer import Tracer
from auto_qc.pi.engine.validator import validate_output

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量 & 工具函数
# ---------------------------------------------------------------------------

# Phase 2 多视角探索：每个 chunk 从多个质检维度并行探索，取并集
# v3.0: 5 视角 × 1 轮替代原来的 1 视角 × 2 轮

# Phase 5 验证集采样上限：holdout 超过此条数时随机采样，控制 LLM prompt 大小
HOLDOUT_VERIFY_CAP = 1000


def _merge_patterns(pattern_list: list[dict]) -> list[dict]:
    """按 pattern_name 合并多个探索结果，用于 Phase 2 多轮合并和 Phase 3 去重。"""
    seen: dict[str, dict] = {}
    for p in pattern_list:
        name = p.get("pattern_name", "unknown")
        if name not in seen:
            seen[name] = dict(p)
            # 初始化 pattern_ids 列表，包含自身的 pattern_id
            pid = p.get("pattern_id", "")
            seen[name]["pattern_ids"] = [pid] if pid else []
        else:
            existing = seen[name]
            existing["found_count"] = existing.get("found_count", 0) + p.get("found_count", 0)
            existing["total_checked"] = existing.get("total_checked", 0) + p.get("total_checked", 0)
            existing_ids = {e.get("dialogue_id") for e in existing.get("examples", [])}
            for ex in p.get("examples", []):
                if ex.get("dialogue_id") not in existing_ids:
                    existing["examples"].append(ex)
            existing["examples"] = existing["examples"][:5]
            severity_order = {"high": 3, "medium": 2, "low": 1}
            if severity_order.get(p.get("severity", "low"), 0) > severity_order.get(existing.get("severity", "low"), 0):
                existing["severity"] = p["severity"]
            # 合并 pattern_ids
            pid = p.get("pattern_id", "")
            if pid and pid not in existing.get("pattern_ids", []):
                existing.setdefault("pattern_ids", []).append(pid)
    return sorted(seen.values(), key=lambda x: x.get("found_count", 0), reverse=True)



PHASE_NAMES = {
    1: "数据预处理",
    2: "探索发现",
    3: "主题聚类",
    4: "规则生成",
    5: "规则验证",
    6: "规则输出",
}


def setup_logging(run_dir: Path) -> None:
    """配置日志输出到文件和终端。"""
    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if logging.getLogger().hasHandlers():
        return
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_dir / "harness.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def get_run_id() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def get_run_dir(run_id: str, output_base: Path | None = None) -> Path:
    base = output_base or (Path(__file__).parent.parent / "output")
    return base / run_id


def get_base_dir() -> Path:
    """PI 模块根目录。"""
    return Path(__file__).parent.parent


def set_latest_link(run_dir: Path) -> None:
    """创建 latest 软链接指向当前运行目录。"""
    link = run_dir.parent / "latest"
    if link.exists() or link.is_symlink():
        link.unlink()
    try:
        link.symlink_to(run_dir, target_is_directory=True)
    except OSError:
        (run_dir.parent / "latest.txt").write_text(str(run_dir), encoding="utf-8")


# ---------------------------------------------------------------------------
# 运行状态（轻量级，不依赖旧的 state.py）
# ---------------------------------------------------------------------------

class RunState:
    """轻量运行状态。"""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.started_at = datetime.now().isoformat()
        self.phases: dict = {}
        for i in range(1, 7):
            self.phases[str(i)] = {
                "status": "pending",
                "started_at": None,
                "finished_at": None,
            }

    def mark_running(self, phase: int):
        self.phases[str(phase)]["status"] = "running"
        self.phases[str(phase)]["started_at"] = datetime.now().isoformat()

    def mark_completed(self, phase: int, result: dict | None = None):
        self.phases[str(phase)]["status"] = "completed"
        self.phases[str(phase)]["finished_at"] = datetime.now().isoformat()
        if result:
            self.phases[str(phase)]["result"] = result

    def mark_failed(self, phase: int, error: str = ""):
        self.phases[str(phase)]["status"] = "failed"
        self.phases[str(phase)]["finished_at"] = datetime.now().isoformat()
        self.phases[str(phase)]["error"] = error

    def save(self, run_dir: Path):
        state_file = run_dir / ".state.json"
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump({
                "run_id": self.run_id,
                "started_at": self.started_at,
                "phases": self.phases,
            }, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, run_dir: Path) -> "RunState | None":
        state_file = run_dir / ".state.json"
        if not state_file.exists():
            return None
        with open(state_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
        state = cls(raw["run_id"])
        state.started_at = raw.get("started_at", "")
        state.phases = raw.get("phases", {})
        return state


# ---------------------------------------------------------------------------
# Phase 执行函数
# ---------------------------------------------------------------------------

def run_phase1(config: HarnessConfig, run_dir: Path, state: RunState, domain, tracer: Tracer) -> bool:
    """Phase 1: 数据预处理。"""
    print(f"\n[Phase 1] 数据预处理... ", end="", flush=True)
    state.mark_running(1)
    state.save(run_dir)

    adapter = domain.data_adapter
    records = adapter.parse(config.data.input_path)
    if not records:
        print("失败: 没有解析到任何对话")
        state.mark_failed(1, "没有解析到任何对话")
        state.save(run_dir)
        return False

    criteria = domain.quality_criteria
    filter_tts = criteria.get("filter_tts_symbols", False)
    detect_hangup = criteria.get("detect_user_hangup", False)

    dialogues = []
    for r in records:
        turns = []
        for t in r.turns:
            content = t.content
            if filter_tts and content:
                content = adapter.preprocess_text(content)
            if t.role == "ai":
                turns.append({"ttsResult": content, "asrResult": ""})
            else:
                turns.append({"ttsResult": "", "asrResult": content})
        dialogues.append({"id": r.id, "turns": turns, "user_hangup": detect_hangup and adapter.is_user_hangup(r)})

    chunk_size = config.data.chunk_size
    if chunk_size <= 0:
        from auto_qc.pi.agents.config import compute_chunk_size
        chunk_size = compute_chunk_size(len(dialogues))

    phase1_dir = run_dir / "phase1"
    phase1_dir.mkdir(parents=True, exist_ok=True)
    chunk_count = split_into_chunks(dialogues, chunk_size, phase1_dir)

    # 校验 Phase 1 输出
    valid, msg = validate_output("phase1", phase1_dir)
    if not valid:
        print(f"失败: Phase 1 输出校验不通过 - {msg}")
        state.mark_failed(1, f"phase1_validation_failed: {msg}")
        state.save(run_dir)
        return False

    # 保留 10% chunk 作为验证集（不参与 Phase 2 探索）
    # 10% 的比例在各种数据量下都能兼顾探索和验证
    HOLDOUT_PERCENT = 10
    random.seed(42)
    all_chunks = sorted(phase1_dir.glob("chunk_*.jsonl"))
    n_holdout = max(1, len(all_chunks) * HOLDOUT_PERCENT // 100)
    for f in random.sample(all_chunks, n_holdout):
        f.rename(f.parent / f"holdout_{f.name}")

    report = generate_phase1_report(
        total=len(dialogues), success=len(dialogues), errors=[],
        chunk_count=chunk_count,
        avg_turns=sum(len(d["turns"]) for d in dialogues) / len(dialogues),
    )
    (run_dir / "summaries").mkdir(parents=True, exist_ok=True)
    (run_dir / "summaries" / "phase1_summary.md").write_text(report, encoding="utf-8")

    state.mark_completed(1, {"chunk_count": chunk_count, "dialogue_count": len(dialogues)})
    state.save(run_dir)
    print(f"{len(dialogues)} 条对话 → {chunk_count} 个 chunk（{n_holdout} 个保留为验证集）")
    return True


def run_phase2(scheduler: Scheduler, config: HarnessConfig, run_dir: Path, state: RunState, domain, tracer: Tracer) -> bool:
    """Phase 2: 多视角并行探索（v3.0）。

    每个 chunk 从多个质检维度（合规红线、流畅度、语用、逻辑、专业度）
    同时探索，各视角独立运行后合并去重。替代 v2.0 的单视角 2 轮模式。
    """
    chunk_files = sorted((run_dir / "phase1").glob("chunk_*.jsonl"))
    if not chunk_files:
        print("[Phase 2] 探索发现... 失败: 没有 chunk 文件")
        return False

    explorers = domain.prompts.explorers  # [(name, prompt, temperature), ...]
    total = len(chunk_files)
    completed = 0
    all_patterns = []

    # 找出已完成的 chunk（断点续传）
    done = set()
    for cf in chunk_files:
        df = cf.with_suffix(".discovery.json")
        if df.exists():
            try:
                with open(df, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    done.add(cf.name)
                    all_patterns.extend(data)
                    tracer.record_phase2_chunk(cf.name, data)
            except Exception:
                pass

    pending = [cf for cf in chunk_files if cf.name not in done]
    if not pending:
        print(f"[Phase 2] 探索发现... {total}/{total} 已完成 ({len(all_patterns)} 个模式)")
        state.mark_completed(2, {"pattern_count": len(all_patterns)})
        state.save(run_dir)
        return True

    print(f"[Phase 2] 探索发现... 0/{total}（{len(explorers)} 视角）", end="", flush=True)

    while pending:
        batch = pending[:scheduler.config.concurrency]

        # 构建所有视角 × 所有 chunk 的任务列表（全部并发）
        all_tasks = []
        for cf in batch:
            chunk_content = cf.read_text(encoding="utf-8")
            for persp_name, persp_prompt, persp_temp in explorers:
                prompt = f"""{persp_prompt}

以下是待分析的对话数据（JSONL 格式，每行一条完整对话）：
{chunk_content}

请基于以上数据，按照上述标准进行分析，找出存在的 AI 发言问题模式。

## 输出约束（非常重要）
在你的最终回复中**只输出 JSON 数组结果**，不要包含任何其他文字、解释或说明。"""

                output_file = cf.with_suffix(f'.discovery_{persp_name}.json')
                all_tasks.append({
                    "user_prompt": prompt,
                    "output_file": str(output_file.absolute()),
                    "chunk_name": cf.name,
                    "perspective": persp_name,
                    "temperature": persp_temp,
                    "system_prompt": f"你是电话招聘场景的质检专家，专注于{persp_name}维度。",
                })

        results = scheduler.run_tasks_batch(
            tasks=all_tasks,
            system_prompt="你是电话招聘场景的质检专家。",
            validator_phase="explorer",
        )

        # 按 chunk 收集各视角结果
        chunk_patterns: dict[str, list[dict]] = {}
        for (success, data, error), task in zip(results, all_tasks):
            if success and data:
                chunk_patterns.setdefault(task["chunk_name"], []).extend(data)

        # 合并各视角结果（按 pattern_name 去重），写入最终 .discovery.json
        for cf in batch:
            patterns = chunk_patterns.get(cf.name, [])
            if patterns:
                merged = _merge_patterns(patterns)
                tracer.record_phase2_chunk(cf.name, merged)
                merged_file = cf.with_suffix('.discovery.json')
                with open(merged_file, "w", encoding="utf-8") as f:
                    json.dump(merged, f, ensure_ascii=False, indent=2)
                all_patterns.extend(merged)
                completed += 1
            else:
                print(f"  ⚠ chunk {cf.name} 所有视角探索均失败")

        pending = pending[len(batch):]
        print(f"\r[Phase 2] 探索发现... {completed + len(done)}/{total} ", end="", flush=True)

    print(f"\r[Phase 2] 探索发现... {completed + len(done)}/{total} ({len(all_patterns)} 个模式)")
    state.mark_completed(2, {"pattern_count": len(all_patterns)})
    state.save(run_dir)
    return len(all_patterns) > 0


# Phase 3 单批最大模式数：超过此值拆分为多批 + 合并步骤
# DeepSeek v4-flash 1M 上下文，每批可容纳 200+ 个模式
# 减少分批数 → 降低跨批合并级联误差
# v3.0: 多视角探索预计产生 3-5× 模式，提高到 800（~88K tokens，仍远低于 1M 上限）
# 521 模式一次性聚类约 55K tokens，远低于 1M 上限
PHASE3_BATCH_SIZE = 800


def _build_pattern_summary(patterns: list[dict]) -> list[dict]:
    """构建聚类用的简明模式摘要（只给 LLM 必要字段，description 截断减少 token）。"""
    summary = []
    for p in patterns:
        desc = p.get("description", "")
        summary.append({
            "pattern_id": p.get("pattern_id", ""),
            "pattern_name": p.get("pattern_name", ""),
            "description": desc[:80] + ("…" if len(desc) > 80 else ""),
        })
    return summary


def run_phase3(scheduler: Scheduler, run_dir: Path, state: RunState, domain, tracer: Tracer) -> bool:
    """Phase 3: 主题聚类 — 两阶段策略。

    1. 分批聚类：模式 > PHASE3_BATCH_SIZE 时拆分为多批，每批独立聚类
       （防止单次 LLM prompt 过大导致 API 504 超时）
    2. 跨批合并：所有批的聚类结果合并后，调用 LLM 合并跨批的相似主题
    """
    print("[Phase 3] 主题聚类...", flush=True)

    # 加载所有发现模式
    all_discoveries = []
    for cf in sorted((run_dir / "phase1").glob("chunk_*.jsonl")):
        df = cf.with_suffix(".discovery.json")
        if df.exists():
            try:
                with open(df, "r", encoding="utf-8") as f:
                    all_discoveries.extend(json.load(f))
            except Exception:
                pass

    if not all_discoveries:
        print("失败: 没有找到探索结果")
        return False

    unique_patterns = _merge_patterns(all_discoveries)

    phase3_dir = run_dir / "phase3"
    phase3_dir.mkdir(parents=True, exist_ok=True)

    # 保存合并后的模式列表（供 Phase 4 查找完整数据）
    pattern_index = {p.get("pattern_id", ""): p for p in unique_patterns if p.get("pattern_id")}
    with open(phase3_dir / "unique_patterns.json", "w", encoding="utf-8") as f:
        json.dump(unique_patterns, f, ensure_ascii=False, indent=2)

    total = len(unique_patterns)
    if total <= PHASE3_BATCH_SIZE:
        # 模式少，直接单批聚类
        pattern_summary = _build_pattern_summary(unique_patterns)
        prompt = f"""{domain.prompts.topic_clustering}

## 模式数据
{json.dumps(pattern_summary, ensure_ascii=False, indent=2)}"""

        output_file = phase3_dir / "topic_clusters.json"
        success, clusters, error = scheduler.run_task(
            system_prompt="你是质检问题模式聚类专家，严格按用户要求输出 JSON。",
            user_prompt=prompt,
            output_file=output_file,
            validator_phase="topic_clustering",
            temperature=0.3,
        )

        if not success:
            print(f"  LLM 聚类失败: {error}")
            return False

        print(f"  {total} 个模式 → {len(clusters)} 个主题组")
        state.mark_completed(3, {"unique_count": total, "cluster_count": len(clusters)})
        state.save(run_dir)
        return True

    # ==========================================================================
    # 模式多 → 两阶段聚类
    # ==========================================================================
    print(f"  模式较多 ({total} 个)，拆分为多批聚类...")

    # Step 1: 分批独立聚类
    batches = [unique_patterns[i:i + PHASE3_BATCH_SIZE]
               for i in range(0, total, PHASE3_BATCH_SIZE)]
    all_batch_clusters = []  # [(batch_idx, cluster), ...]
    # 记录每批中每个 pattern_id 所属的 batch，用于 Step 2 跨批合并
    batch_of_pattern: dict[str, int] = {}
    for batch_idx, batch in enumerate(batches):
        for p in batch:
            batch_of_pattern[p.get("pattern_id", "")] = batch_idx

    for batch_idx, batch in enumerate(batches):
        pattern_summary = _build_pattern_summary(batch)
        prompt = f"""{domain.prompts.topic_clustering}

## 模式数据
{json.dumps(pattern_summary, ensure_ascii=False, indent=2)}"""

        output_file = phase3_dir / f"topic_clusters_batch{batch_idx}.json"
        success, clusters, error = scheduler.run_task(
            system_prompt="你是质检问题模式聚类专家，严格按用户要求输出 JSON。",
            user_prompt=prompt,
            output_file=output_file,
            validator_phase="topic_clustering",
            temperature=0.3,
        )

        if not success:
            print(f"  第 {batch_idx+1}/{len(batches)} 批聚类失败: {error}")
            return False

        for c in clusters:
            all_batch_clusters.append((batch_idx, c))
        print(f"  第 {batch_idx+1}/{len(batches)} 批: {len(batch)} 个模式 → {len(clusters)} 个主题组")

    # Step 2: 增量跨批合并（逐批合并，避免一次处理所有 batch 导致 prompt 过大超时）
    # 从第一批开始，每次合并当前结果 + 下一批
    merged_clusters = [c for _, c in all_batch_clusters if _ == 0]
    remaining_batches = sorted(set(b for b, _ in all_batch_clusters))[1:]
    output_file = phase3_dir / "topic_clusters.json"

    merge_prompt = domain.prompts.merge_clusters

    for next_batch in remaining_batches:
        next_clusters = [c for _, c in all_batch_clusters if _ == next_batch]
        merge_input = {
            "已有主题组": [{"topic_name": c.get("topic_name", ""), "pattern_ids": c.get("pattern_ids", []), "pattern_names": c.get("pattern_names", [])} for c in merged_clusters],
            "新批次主题组": [{"topic_name": c.get("topic_name", ""), "pattern_ids": c.get("pattern_ids", []), "pattern_names": c.get("pattern_names", [])} for c in next_clusters],
        }
        merge_user_prompt = merge_prompt + "\n\n" + json.dumps(merge_input, ensure_ascii=False, indent=2)
        success, merged_result, error = scheduler.run_task(
            system_prompt="你是质检问题模式聚类专家，严格按用户要求输出 JSON，只做合并操作。",
            user_prompt=merge_user_prompt,
            output_file=phase3_dir / f"topic_clusters_merged_batch{next_batch}.json",
            validator_phase="topic_clustering",
            temperature=0.2,
        )
        if success and merged_result:
            merged_clusters = merged_result
            print(f"  增量合并第 {next_batch+1} 批完成 → {len(merged_clusters)} 个主题组")
        else:
            # 合并失败时，基于 topic_name 做本地去重追加（而非盲目追加）
            print(f"  增量合并第 {next_batch+1} 批失败 ({error})，本地去重追加")
            existing_names = {c.get("topic_name", "") for c in merged_clusters}
            for nc in next_clusters:
                if nc.get("topic_name", "") not in existing_names:
                    merged_clusters.append(nc)
                    existing_names.add(nc.get("topic_name", ""))

    # 写入最终合并结果
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(merged_clusters, f, ensure_ascii=False, indent=2)

    print(f"  {total} 个模式 → {len(merged_clusters)} 个主题组"
          f"（{len(batches)} 批，{'已合并' if success else '未合并'}）")
    state.mark_completed(3, {"unique_count": total, "cluster_count": len(merged_clusters)})
    state.save(run_dir)
    return True


def run_phase4(scheduler: Scheduler, run_dir: Path, state: RunState, domain, tracer: Tracer) -> bool:
    """Phase 4: 规则生成 — 每个主题聚类生成一条质检规则。"""
    phase3_dir = run_dir / "phase3"
    clusters_file = phase3_dir / "topic_clusters.json"
    patterns_file = phase3_dir / "unique_patterns.json"

    if not clusters_file.exists() or not patterns_file.exists():
        print("[Phase 4] 规则生成... 失败: topic_clusters.json 或 unique_patterns.json 不存在")
        return False

    with open(clusters_file, "r", encoding="utf-8") as f:
        clusters = json.load(f)
    with open(patterns_file, "r", encoding="utf-8") as f:
        unique_patterns = json.load(f)

    # 建立 pattern_id → 完整数据的索引
    pattern_index = {p.get("pattern_id", ""): p for p in unique_patterns if p.get("pattern_id")}

    phase4_dir = run_dir / "phase4"
    phase4_dir.mkdir(parents=True, exist_ok=True)
    draft_file = phase4_dir / "draft_rules.json"
    if draft_file.exists():
        print(f"[Phase 4] 规则生成... 已完成 (draft_rules.json 已存在)")
        state.mark_completed(4, {})
        state.save(run_dir)
        return True

    all_rules = []
    for cluster in clusters:
        topic = cluster.get("topic_name", "未命名主题")
        pattern_ids = cluster.get("pattern_ids", [])

        # 按 pattern_id 查找完整模式数据
        cluster_patterns = []
        for pid in pattern_ids:
            p = pattern_index.get(pid)
            if p:
                cluster_patterns.append(p)

        if not cluster_patterns:
            logger.warning(f"  主题 '{topic}' 没有找到对应的模式数据，跳过")
            continue

        batch_label = f"cluster_{topic[:10]}"
        output_file = phase4_dir / f"rules_{batch_label}.json"
        if output_file.exists():
            try:
                with open(output_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data:
                    for rule in data:
                        rule["merged_from"] = pattern_ids
                    all_rules.extend(data)
                    continue
            except Exception:
                pass

        cluster_data_json = json.dumps(cluster_patterns, ensure_ascii=False, indent=2)
        prompt = f"""{domain.prompts.aggregator}

以下模式来自同一主题聚类「{topic}」，请将这些模式合并生成一条质检规则（不要拆分为多条）。

## 模式数据
{cluster_data_json}

## 输出约束（非常重要）
在你的最终回复中**只输出 JSON 数组结果**，不要包含任何其他文字、解释或说明。"""

        tasks = [{
            "user_prompt": prompt,
            "output_file": str(output_file.absolute()),
        }]

        results = scheduler.run_tasks_batch(
            tasks=tasks,
            system_prompt="你是质检规则聚合专家。",
            validator_phase="aggregator",
            temperature=0.3,
        )

        success, data, error = results[0]
        if success and data:
            for rule in data:
                rule["merged_from"] = pattern_ids
            all_rules.extend(data)
            print(f"  {topic} → {len(data)} rules")
        else:
            logger.warning(f"  主题 '{topic}' 聚合失败: {error}")

    if not all_rules:
        print("[Phase 4] 规则生成... 失败: 没有生成任何规则")
        return False

    # 按支持对话数量降序排列
    all_rules.sort(key=lambda r: r.get("support_count", 0), reverse=True)

    # 过滤低支持规则：支持对话 < 10 条的规则属于碎片噪声，丢弃
    MIN_SUPPORT = 10
    before = len(all_rules)
    all_rules = [r for r in all_rules if r.get("support_count", 0) >= MIN_SUPPORT]
    if before - len(all_rules) > 0:
        print(f"  过滤了 {before - len(all_rules)} 条低支持噪声规则 (support_count < {MIN_SUPPORT})")

    # 重新分配 rule_id
    for i, rule in enumerate(all_rules, 1):
        rule["rule_id"] = f"RULE-{i:03d}"

    # 用最终 rule_id 记录 chunk 映射
    for rule in all_rules:
        tracer.record_phase4_batch("final", [rule])

    with open(draft_file, "w", encoding="utf-8") as f:
        json.dump(all_rules, f, ensure_ascii=False, indent=2)

    # 记录最终规则的追溯信息
    tracer.record_phase4_rules(all_rules)

    state.mark_completed(4, {"rule_count": len(all_rules)})
    state.save(run_dir)
    print(f"[Phase 4] 规则生成... {len(all_rules)} 条规则 → draft_rules.json")
    return True


def run_phase5(scheduler: Scheduler, run_dir: Path, state: RunState, domain, tracer: Tracer) -> bool:
    """Phase 5: 规则验证（分层抽样）。"""
    draft_file = run_dir / "phase4" / "draft_rules.json"
    if not draft_file.exists():
        print("[Phase 5] 规则验证... 失败: draft_rules.json 不存在")
        return False

    with open(draft_file, "r", encoding="utf-8") as f:
        rules = json.load(f)

    phase5_dir = run_dir / "phase5"
    phase5_dir.mkdir(parents=True, exist_ok=True)
    results_file = phase5_dir / "validation_results.json"
    if results_file.exists():
        print(f"[Phase 5] 规则验证... 已完成 (validation_results.json 已存在)")
        state.mark_completed(5, {})
        state.save(run_dir)
        return True

    # 所有规则 100% 验证（不进行分层抽样，确保每条输出规则都被验证）
    sampled = list(rules)

    chunk_files = sorted((run_dir / "phase1").glob("chunk_*.jsonl"))
    if not chunk_files:
        print("[Phase 5] 规则验证... 失败: 没有 chunk 文件")
        return False

    criteria = domain.quality_criteria
    thresholds = criteria.get("thresholds", {})

    # 预加载所有 chunk 数据（按文件名索引）
    chunk_data: dict[str, list[str]] = {}
    for cf in chunk_files:
        lines = cf.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            chunk_data[cf.name] = lines

    # 预加载验证集（holdout chunk — 未参与 Phase 2 探索，用于评估规则泛化能力）
    holdout_lines: list[str] = []
    for cf in sorted((run_dir / "phase1").glob("holdout_chunk_*.jsonl")):
        lines = cf.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            holdout_lines.extend(lines)

    if holdout_lines:
        if len(holdout_lines) > HOLDOUT_VERIFY_CAP:
            print(f"  [Phase 5] 验证集: {len(holdout_lines)} 条对话（采样 {HOLDOUT_VERIFY_CAP} 条送入 LLM）")
        else:
            print(f"  [Phase 5] 验证集: {len(holdout_lines)} 条对话（LLM 未见过）")

    all_results = []
    # 分批验证（每批最多 concurrency 个）
    for start in range(0, len(sampled), scheduler.config.concurrency):
        batch = sampled[start:start + scheduler.config.concurrency]
        tasks = []
        for rule in batch:
            rule_id = rule.get("rule_id", "")
            rule_chunks = tracer.get_rule_chunks(rule_id)
            sample_lines = []

            if holdout_lines:
                # 有验证集：从 holdout 中采样，控制 LLM prompt 大小
                if len(holdout_lines) <= HOLDOUT_VERIFY_CAP:
                    sample_lines = holdout_lines
                else:
                    sample_lines = random.sample(holdout_lines, HOLDOUT_VERIFY_CAP)
            elif rule_chunks:
                # 没有验证集（旧版数据兼容）：按来源 chunk 全量
                for cn in rule_chunks:
                    lines = chunk_data.get(cn, [])
                    if lines:
                        sample_lines.extend(lines)
            else:
                # 兜底：没有追溯信息时从所有对话中使用
                for lines in chunk_data.values():
                    sample_lines.extend(lines)

            if not sample_lines:
                continue
            sample_data = "\n".join(sample_lines)

            sev = rule.get("confidence", "low")
            sev_thresholds = thresholds.get(sev, thresholds.get("medium", {}))
            rejected_rate = sev_thresholds.get("rejected", 0.005)

            output_file = phase5_dir / f"validation_{rule['rule_id']}.json"
            prompt = f"""{domain.prompts.verifier}

规则:
- Rule ID: {rule.get('rule_id', '')}
- Rule Name: {rule.get('rule_name', '')}
- Description: {rule.get('description', '')}
- Detection Logic: {rule.get('detection_logic', '')}
- 规则严重度: {sev}

验证数据（JSONL 格式，每行一条完整对话）：
{sample_data}

判定标准（根据规则严重度 {sev} 定制）：
- valid: 命中率 >= {rejected_rate:.1%}
- too_narrow: 命中率 < {rejected_rate:.1%}

## 输出约束（非常重要）
在你的最终回复中**只输出 JSON 数组结果**，不要包含任何其他文字、解释或说明。"""

            tasks.append({
                "user_prompt": prompt,
                "output_file": str(output_file.absolute()),
                "rule_id": rule["rule_id"],
            })

        results = scheduler.run_tasks_batch(
            tasks=tasks,
            system_prompt="你是质检规则验证专家。",
            validator_phase="verifier",
        )

        for (success, data, error), task in zip(results, tasks):
            if success and data:
                if isinstance(data, list):
                    for item in data:
                        all_results.append(item)
                        tracer.record_phase5_validation(task["rule_id"], item)
                elif isinstance(data, dict):
                    all_results.append(data)
                    tracer.record_phase5_validation(task["rule_id"], data)

    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    valid_count = sum(1 for r in all_results if r.get("status") == "valid")
    print(f"[Phase 5] 规则验证... {len(all_results)} 条验证完成 ({valid_count} valid)")
    state.mark_completed(5, {"validation_count": len(all_results)})
    state.save(run_dir)
    return True


def run_phase6(config: HarnessConfig, run_dir: Path, state: RunState) -> bool:
    """Phase 6: 规则输出。"""
    print("[Phase 6] 规则输出... ", end="", flush=True)

    draft_file = run_dir / "phase4" / "draft_rules.json"
    if not draft_file.exists():
        print("失败: draft_rules.json 不存在")
        return False

    with open(draft_file, "r", encoding="utf-8") as f:
        rules = json.load(f)

    validation_results = []
    results_file = run_dir / "phase5" / "validation_results.json"
    if results_file.exists():
        try:
            with open(results_file, "r", encoding="utf-8") as f:
                validation_results = json.load(f)
        except Exception:
            pass

    phase1_result = state.phases.get("1", {}).get("result", {})
    metadata = {
        "run_id": state.run_id,
        "data_source": str(config.data.input_path),
        "total_dialogues": phase1_result.get("dialogue_count", 0),
    }
    generate_rules_md(rules, validation_results, metadata, run_dir)
    generate_rules_summary(rules, metadata, run_dir)

    # 校验 Phase 6 输出
    p6_valid, p6_msg = validate_output("phase6_output", run_dir / "phase6")
    if not p6_valid:
        logger.warning(f"Phase 6 输出校验失败: {p6_msg}")
    else:
        logger.info("Phase 6 输出校验通过")

    rule_count = sum(1 for r in rules if not any(
        vr.get("rule_id") == r["rule_id"] and vr.get("status") != "valid"
        for vr in validation_results
    ))
    print(f"rules.md 已生成 ({rule_count} 条有效规则)")
    state.mark_completed(6, {"rule_count": rule_count})
    state.save(run_dir)
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Windows 下确保 stdout/stderr 输出 UTF-8（仅在直接运行时设置，不影响 pytest）
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

    parser = argparse.ArgumentParser(description="AI Quality Harness v2 — 一键运行 Pipeline")
    parser.add_argument("--status", action="store_true", help="查看最近一次运行状态")
    parser.add_argument("--resume", action="store_true", help="从断点续传")
    parser.add_argument("--config", type=str, default=None, help="配置文件路径")
    parser.add_argument("--data", type=str, default=None, help="输入数据 Excel 文件路径（覆盖 config.yaml）")
    parser.add_argument("--domain", type=str, default=None, help="领域名称（覆盖 config.yaml）")
    parser.add_argument("--output", type=str, default=None, help="输出目录（覆盖默认 pi/output/）")
    args = parser.parse_args()

    base_dir = get_base_dir()
    config_path = args.config or str(base_dir / "agents" / "config.yaml")
    config = HarnessConfig.from_yaml(config_path)

    # CLI 参数覆盖 config 设置
    if args.data:
        config.data.input_path = args.data
    if args.domain:
        config.data.domain = args.domain
    output_base = Path(args.output) if args.output else None

    if args.status:
        base_dir = get_base_dir()
        run_dir = base_dir / "output" / "latest"
        if run_dir.is_symlink():
            run_dir = run_dir.resolve()
        state = RunState.load(run_dir)
        if state:
            for phase_num in range(1, 7):
                ps = state.phases.get(str(phase_num), {})
                status = ps.get("status", "pending")
                icon = {"completed": "[OK]", "running": "[..]", "pending": "[  ]", "failed": "[!!]"}.get(status, "[  ]")
                print(f"  Phase {phase_num} {PHASE_NAMES.get(phase_num, ''):<10} {icon} {status}")
        else:
            print("没有找到运行状态。")
        return

    # 确定运行目录
    if args.resume:
        if output_base:
            # 自定义输出路径：读取 latest.txt
            latest_txt = output_base / "latest.txt"
            if latest_txt.exists():
                run_dir = Path(latest_txt.read_text(encoding="utf-8").strip())
                if not run_dir.is_absolute():
                    run_dir = output_base.parent / run_dir
            else:
                logger.error(f"没有找到可续传的运行状态（{latest_txt} 不存在）。")
                return
        else:
            base_dir = get_base_dir()
            run_dir = base_dir / "output" / "latest"
            if run_dir.is_symlink():
                run_dir = run_dir.resolve()
        state = RunState.load(run_dir)
        if not state:
            logger.error("没有找到可续传的运行状态。")
            return
        run_id = state.run_id
    else:
        run_id = get_run_id()
        run_dir = get_run_dir(run_id, output_base)
        run_dir.mkdir(parents=True, exist_ok=True)
        set_latest_link(run_dir)
        state = RunState(run_id)
        state.save(run_dir)

    setup_logging(run_dir)
    logger.info(f"运行 ID: {run_id}")
    logger.info(f"输出目录: {run_dir}")

    # 初始化组件
    domain = load_domain(config.data.domain)
    tracer = Tracer(run_id)
    scheduler = Scheduler(config.llm)

    print(f"\n{'=' * 50}")
    print(f"auto-pi v2 Pipeline 开始运行")
    print(f"运行 ID: {run_id}")
    print(f"{'=' * 50}")

    # Phase 1
    if state.phases.get("1", {}).get("status") != "completed":
        if not run_phase1(config, run_dir, state, domain, tracer):
            logger.error("Phase 1 失败，终止运行")
            return

    # Phase 2
    if state.phases.get("2", {}).get("status") != "completed":
        if not run_phase2(scheduler, config, run_dir, state, domain, tracer):
            logger.error("Phase 2 失败，终止运行")
            return

    # Phase 3
    if state.phases.get("3", {}).get("status") != "completed":
        if not run_phase3(scheduler, run_dir, state, domain, tracer):
            logger.error("Phase 3 失败，终止运行")
            return

    # Phase 4
    if state.phases.get("4", {}).get("status") != "completed":
        if not run_phase4(scheduler, run_dir, state, domain, tracer):
            logger.error("Phase 4 失败，终止运行")
            return

    # Phase 5
    if state.phases.get("5", {}).get("status") != "completed":
        if not run_phase5(scheduler, run_dir, state, domain, tracer):
            logger.error("Phase 5 失败，终止运行")
            return

    # Phase 6
    if state.phases.get("6", {}).get("status") != "completed":
        if not run_phase6(config, run_dir, state):
            logger.error("Phase 6 失败，终止运行")
            return

    # 保存追溯数据
    tracer.save(run_dir)

    print(f"\n{'=' * 50}")
    print(f"全部完成！")
    print(f"规则文件: {run_dir / 'phase6' / 'rules.md'}")
    print(f"追溯文件: {run_dir / 'trace' / 'trace.json'}")
    logger.info(str(scheduler.usage))  # 记录到日志文件
    print(f"\n{str(scheduler.usage)}")  # 显示到终端
    print(f"{'=' * 50}")


# ---------------------------------------------------------------------------
# 可调用入口（供 CLI / Web UI 使用）
# ---------------------------------------------------------------------------

def run_pipeline(
    data_path: str,
    output_dir: str | None = None,
    domain: str = "recruitment",
) -> dict:
    """供 CLI 和 Web UI 调用的统一入口。

    Args:
        data_path: 输入数据 Excel 文件路径
        output_dir: 输出目录（可选，默认在 pi/output/ 下按时间创建）
        domain: 领域名称，默认 recruitment

    Returns:
        包含运行结果和状态的 dict
    """
    # 确保 .env 环境变量已加载到进程环境
    from auto_qc.core.config import load_env_config
    load_env_config()

    # 创建配置
    config = HarnessConfig()
    config.data.input_path = data_path
    config.data.domain = domain

    # 设置运行目录
    run_id = get_run_id()
    output_base = Path(output_dir) if output_dir else None
    run_dir = get_run_dir(run_id, output_base)
    run_dir.mkdir(parents=True, exist_ok=True)
    set_latest_link(run_dir)

    # 初始化
    setup_logging(run_dir)
    state = RunState(run_id)
    state.save(run_dir)

    logger.info(f"运行 ID: {run_id}")
    logger.info(f"输出目录: {run_dir}")

    domain_plugin = load_domain(domain)
    tracer = Tracer(run_id)
    scheduler = Scheduler(config.llm)

    # 运行各 Phase
    results = {}
    all_ok = True
    for phase_num, (name, phase_func) in {
        1: ("数据预处理", lambda: run_phase1(config, run_dir, state, domain_plugin, tracer)),
        2: ("探索发现", lambda: run_phase2(scheduler, config, run_dir, state, domain_plugin, tracer)),
        3: ("主题聚类", lambda: run_phase3(scheduler, run_dir, state, domain_plugin, tracer)),
        4: ("规则生成", lambda: run_phase4(scheduler, run_dir, state, domain_plugin, tracer)),
        5: ("规则验证", lambda: run_phase5(scheduler, run_dir, state, domain_plugin, tracer)),
        6: ("规则输出", lambda: run_phase6(config, run_dir, state)),
    }.items():
        phase_key = f"phase{phase_num}"
        if state.phases.get(str(phase_num), {}).get("status") == "completed":
            results[phase_key] = {"name": name, "success": True, "skipped": True}
            continue
        try:
            success = phase_func()
            results[phase_key] = {"name": name, "success": success}
            if not success:
                all_ok = False
                break
        except Exception as e:
            logger.error(f"Phase {phase_num} 异常: {e}")
            state.mark_failed(phase_num, str(e))
            results[phase_key] = {"name": name, "success": False, "error": str(e)}
            all_ok = False
            break

    tracer.save(run_dir)

    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "results": results,
        "token_usage": str(scheduler.usage),
        "status": "completed" if all_ok else "failed",
    }


if __name__ == "__main__":
    main()
