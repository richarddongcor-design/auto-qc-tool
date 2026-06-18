"""全链路追溯系统。

记录每个数据实体的来源（从哪来、谁处理、用了什么输入），
构建完整的追溯链，最终写入 trace.json。
"""
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class Tracer:
    """追溯记录器。

    每次 Pipeline 运行创建一个 Tracer 实例，记录所有关键决策点的来源信息。
    """

    def __init__(self, run_id: str):
        self.run_id = run_id
        # trace 数据格式: { "RULE-001": { "rule_name": "...", "origin": {...}, "validation": {...} } }
        self.trace: dict = {}
        # 中间追溯数据：记录 chunk → pattern 的映射
        self._chunk_to_patterns: dict[str, list[str]] = {}
        # pattern_name → 来自哪些 chunk（Phase 2 LLM 输出的名称，可能不稳定）
        self._pattern_to_chunks: dict[str, list[str]] = {}
        # pattern_id → 来自哪些 chunk（稳定 ID，全流程可追溯）
        self._pattern_id_to_chunks: dict[str, list[str]] = {}
        # rule → 来自哪些 pattern
        self._rule_to_patterns: dict[str, list[str]] = {}
        # rule → 来自哪些 chunk（通过 pattern 间接追溯）
        self._rule_to_chunks: dict[str, list[str]] = {}
        # 全局 pattern_id 序列计数器
        self._next_pattern_seq: int = 0
        # validation 结果
        self._validation_results: dict[str, dict] = {}

    # ---------------------------------------------------------------------------
    # 追溯记录方法
    # ---------------------------------------------------------------------------

    def record_phase2_chunk(self, chunk_name: str, patterns: list[dict]) -> None:
        """记录 Phase 2 某个 chunk 的发现结果，并为每个 pattern 分配稳定 ID。

        Args:
            chunk_name: chunk 文件名（如 chunk_3.jsonl）
            patterns: 该 chunk 发现的 pattern 列表
        """
        pattern_names = [p.get("pattern_name", "") for p in patterns]
        self._chunk_to_patterns[chunk_name] = pattern_names
        for p in patterns:
            pname = p.get("pattern_name", "")
            # 分配稳定 pattern_id（如果还没有）
            if "pattern_id" not in p:
                self._next_pattern_seq += 1
                p["pattern_id"] = f"PTN-{self._next_pattern_seq:04d}"
            pid = p["pattern_id"]

            # 按 pattern_name 索引（保留兼容，Phase 4 LLM 可能仍用名称引用）
            if pname:
                if pname not in self._pattern_to_chunks:
                    self._pattern_to_chunks[pname] = []
                if chunk_name not in self._pattern_to_chunks[pname]:
                    self._pattern_to_chunks[pname].append(chunk_name)

            # 按 pattern_id 索引（主索引，全流程稳定可追溯）
            if pid not in self._pattern_id_to_chunks:
                self._pattern_id_to_chunks[pid] = []
            if chunk_name not in self._pattern_id_to_chunks[pid]:
                self._pattern_id_to_chunks[pid].append(chunk_name)
        logger.info(f"Tracer:chunk {chunk_name} → {len(pattern_names)} patterns")

    def _resolve_pattern_chunks(self, ref: str) -> set[str]:
        """解析 pattern 引用到 chunk 列表，支持三种匹配方式。

        1. pattern_id 精确匹配（如 "PTN-0001"）
        2. pattern_name 精确匹配（如 "拒绝后仍推进流程"）
        3. pattern_name 模糊匹配（子串包含，作为兜底）
        """
        # 方式 1：按 pattern_id
        if ref in self._pattern_id_to_chunks:
            return set(self._pattern_id_to_chunks[ref])

        # 方式 2：按 pattern_name 精确匹配
        if ref in self._pattern_to_chunks:
            return set(self._pattern_to_chunks[ref])

        # 方式 3：模糊匹配 — 找包含 ref 或被 ref 包含的名称
        for name, chunks in self._pattern_to_chunks.items():
            if ref in name or name in ref:
                return set(chunks)

        return set()

    def record_phase4_batch(self, batch_label: str, rules: list[dict]) -> None:
        """记录 Phase 4 某个 batch 的聚合结果。

        Args:
            batch_label: batch 标识（如 rules_batch_high_0）
            rules: 该 batch 聚合的规则列表
        """
        for rule in rules:
            rule_id = rule.get("rule_id", "")
            merged_from = rule.get("merged_from", [])
            self._rule_to_patterns[rule_id] = merged_from
            # 通过 pattern 追溯到 chunk（ID → name → 模糊 三级匹配）
            chunks = set()
            for ref in merged_from:
                chunks |= self._resolve_pattern_chunks(ref)
            self._rule_to_chunks[rule_id] = sorted(chunks)
        logger.info(f"Tracer:batch {batch_label} → {len(rules)} rules")

    def record_phase4_rules(self, rules: list[dict]) -> None:
        """记录 Phase 4 最终规则（按主题聚类生成的规则追溯）。"""
        for rule in rules:
            rule_id = rule.get("rule_id", "")
            chunks = self._rule_to_chunks.get(rule_id, [])
            self.trace[rule_id] = {
                "rule_name": rule.get("rule_name", ""),
                "origin": {
                    "phase_4_patterns": rule.get("merged_from", []),
                    "phase_2_chunks": sorted(chunks),
                },
                "validation": {},
            }
        logger.info(f"Tracer:Phase 4 → {len(rules)} final rules traced")

    def get_rule_chunks(self, rule_id: str) -> list[str]:
        """获取某条规则关联的 chunk 列表（用于验证抽样）。"""
        entry = self.trace.get(rule_id)
        if entry:
            return entry.get("origin", {}).get("phase_2_chunks", [])
        return []

    def record_phase5_validation(self, rule_id: str, validation: dict) -> None:
        """记录 Phase 5 某个规则的验证结果。

        Args:
            rule_id: 规则 ID
            validation: 验证结果字典
        """
        if rule_id in self.trace:
            self.trace[rule_id]["validation"] = {
                "status": validation.get("status", "unknown"),
                "hit_rate": validation.get("hit_rate", 0),
                "total_checked": validation.get("total_checked", 0),
                "hit_count": validation.get("hit_count", 0),
            }

    # ---------------------------------------------------------------------------
    # 输出
    # ---------------------------------------------------------------------------

    def save(self, run_dir: Path) -> None:
        """将追溯数据写入 trace.json。"""
        trace_dir = run_dir / "trace"
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_file = trace_dir / "trace.json"

        data = {
            "run_id": self.run_id,
            "trace": self.trace,
        }
        with open(trace_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Tracer: trace.json 已保存 ({len(self.trace)} rules)")

    def get_trace_for_rule(self, rule_id: str) -> Optional[dict]:
        """获取某条规则的完整追溯链。"""
        return self.trace.get(rule_id)
