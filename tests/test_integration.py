"""端到端集成测试——验证全流程模块能正确串联"""
import json
import tempfile
from pathlib import Path
import pytest
from auto_qc.domain.rules import parse_rules_file, validate_rule_package
from auto_qc.domain.data_loader import load_conversations, save_batches
from auto_qc.domain.prompts import build_qc_prompt, build_attribution_prompt
from auto_qc.framework.validator import validate_batches, validate_worker_output, validate_merge_results
from auto_qc.framework.worker import extract_json
from auto_qc.framework.coordinator import Coordinator
from auto_qc.framework.progress import create_progress, has_unfinished
from auto_qc.framework.cross_validator import stratified_sample, compare_results


class TestEndToEndDataPipeline:
    """测试数据管道（不调 LLM）"""

    def test_parse_inline_rules(self):
        """测试：内联合规规则解析"""
        import tempfile
        rules_md = """## R01: 测试规则一

**严重程度**: 高

**描述**: 测试描述

**检测逻辑**: 测试逻辑

## R02: 测试规则二

**严重程度**: 中

**描述**: 测试描述二

**检测逻辑**: 测试逻辑二
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8", delete=False) as f:
            f.write(rules_md)
            rules_path = f.name

        try:
            pkg = parse_rules_file(rules_path)
            validate_rule_package(pkg)
            assert len(pkg.rules) == 2
            assert pkg.rule_ids == ["R01", "R02"]
        finally:
            Path(rules_path).unlink()

    def test_coordinator_workflow(self):
        """测试 coordinator 的完整工作流"""
        with tempfile.TemporaryDirectory() as tmpdir:
            coordinator = Coordinator(tmpdir, max_concurrency=5)
            create_progress(tmpdir, total_batches=10)

            # 逐步完成所有批次
            completed = 0
            while True:
                batches = coordinator.get_next_batches()
                if not batches:
                    break
                for bid in batches:
                    coordinator.mark_done(bid)
                    completed += 1

            assert completed == 10
            summary = coordinator.get_summary()
            assert summary["done"] == 10

    def test_has_unfinished_detects(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert not has_unfinished(tmpdir)
            create_progress(tmpdir, total_batches=3, phase="qc")
            assert has_unfinished(tmpdir)

    def test_prompt_building_with_rules(self):
        """测试 prompt 能正确组装规则和对话"""
        from auto_qc.domain.schemas import Batch, Conversation, Rule, RulePackage

        batch = Batch(batch_id=1, conversations=[
            Conversation(id="1", time="2024-01-01", intent="A", conversation="你好"),
        ])
        pkg = RulePackage(rules=[
            Rule(rule_id="R01", name="测试", severity="高",
                 description="测试描述", detection_logic="测试逻辑"),
        ])
        prompt = build_qc_prompt(batch, pkg)
        assert "R01" in prompt
        assert "你好" in prompt
        assert "batch_id" in prompt.lower() or "batch" in prompt.lower()

    def test_worker_output_validation_chain(self):
        """测试 Worker 输出校验的完整链路"""
        # 构造合法输出
        valid = json.dumps({
            "batch_id": 1,
            "rules_checked": ["R01", "R02"],
            "spot_check_details": [
                {"id": "a", "reasoning": "r1"},
                {"id": "b", "reasoning": "r2"},
                {"id": "c", "reasoning": "r3"},
            ],
            "results": [
                {"id": "1", "status": "pass", "violations": []},
                {"id": "2", "status": "violation", "violations": [
                    {"rule_id": "R01", "rule_name": "r", "severity": "高",
                     "evidence": "e", "suggestion": "s"},
                ]},
            ],
        }, ensure_ascii=False)
        output = validate_worker_output(valid, batch_size=2, expected_rule_ids=["R01", "R02"])
        assert output.batch_id == 1
        assert len(output.results) == 2

    def test_cross_validation_pipeline(self):
        """测试交叉验证完整流程"""
        results = (
            [{"id": f"v{i}", "violations": [{"rule_id": "R01"}]} for i in range(20)] +
            [{"id": f"p{i}", "violations": []} for i in range(80)]
        )
        sample = stratified_sample(results, violation_ratio=0.1, non_violation_ratio=0.05)
        assert len(sample) > 0

        # 模拟 recheck 结果（与 original 一致）
        result = compare_results(sample, sample)
        assert result.mismatches == 0
        assert result.status == "ok"
