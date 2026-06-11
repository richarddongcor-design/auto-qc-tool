"""Prompt 模板组装测试"""
import pytest
from auto_qc.domain.prompts import build_single_rule_prompt
from auto_qc.domain.schemas import Rule, Batch, Conversation


def test_build_single_rule_prompt():
    """确保 prompt 包含所有关键占位符的替换结果。"""
    rule = Rule(rule_id="R01", name="测试规则", severity="高",
                description="测试描述", detection_logic="测试逻辑")
    batch = Batch(batch_id=1, conversations=[
        Conversation(id="001", time="", intent="", conversation="对话1"),
        Conversation(id="002", time="", intent="", conversation="对话2"),
    ])
    prompt = build_single_rule_prompt(batch, rule)
    assert "R01" in prompt
    assert "测试规则" in prompt
    assert "001" in prompt
    assert "002" in prompt
    assert "{{" not in prompt  # 无残留占位符
    assert "2" in prompt  # BATCH_SIZE


def test_build_single_rule_prompt_contains_rule_id():
    """单规则 prompt 应包含指定的 rule_id。"""
    rule = Rule(rule_id="auto-pi_R01", name="答非所问", severity="高",
                description="AI 回答与问题无关", detection_logic="检查")
    batch = Batch(batch_id=1, conversations=[
        Conversation(id="001", time="", intent="", conversation="test"),
    ])
    prompt = build_single_rule_prompt(batch, rule)
    assert "auto-pi_R01" in prompt
    assert "答非所问" in prompt


def test_build_single_rule_prompt_only_one_rule():
    """单规则 prompt 中应只包含一条规则的 rule_id，不含数组。"""
    rule = Rule(rule_id="R01", name="测试规则", severity="高",
                description="描述", detection_logic="逻辑")
    batch = Batch(batch_id=1, conversations=[
        Conversation(id="001", time="", intent="", conversation="test"),
    ])
    prompt = build_single_rule_prompt(batch, rule)
    # 规则定义是 JSON 对象（单规则），不是数组格式
    assert '{\n  "rule_id"' in prompt  # 单规则 JSON 对象
    assert '[\n  {\n    "rule_id"' not in prompt  # 非数组
