import json
import pytest
from auto_qc.domain.prompts import build_qc_prompt
from auto_qc.domain.schemas import Batch, Conversation, Rule, RulePackage


def test_build_qc_prompt():
    batch = Batch(batch_id=1, conversations=[
        Conversation(id="1", time="2024-01-01", intent="A", conversation="你好"),
        Conversation(id="2", time="2024-01-02", intent="B", conversation="再见"),
    ])
    pkg = RulePackage(rules=[
        Rule(rule_id="R01", name="规则一", severity="高",
             description="d", detection_logic="l"),
    ])
    prompt = build_qc_prompt(batch, pkg)
    assert "R01" in prompt
    assert "规则一" in prompt
    assert "2024-01-01" in prompt
    assert '"id": "1"' in prompt
