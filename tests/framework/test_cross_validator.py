from auto_qc.framework.cross_validator import fixed_sample, compare_results


def test_fixed_sample():
    results = (
        [{"id": f"v{i}", "violations": [{"rule_id": "R01"}]} for i in range(50)] +
        [{"id": f"p{i}", "violations": []} for i in range(50)]
    )
    sample = fixed_sample(results, sample_size=20)
    assert len(sample) > 0
    assert any(r.get("violations") for r in sample)
    assert any(not r.get("violations") for r in sample)


def test_compare_identical():
    original = [{"id": "1", "violations": [{"rule_id": "R01"}]}]
    recheck = [{"id": "1", "violations": [{"rule_id": "R01"}]}]
    result = compare_results(original, recheck)
    assert result.mismatches == 0
    assert result.discrepancy_rate == 0.0
    assert result.status == "ok"


def test_compare_different():
    original = [{"id": "1", "violations": [{"rule_id": "R01"}]}]
    recheck = [{"id": "1", "violations": []}]
    result = compare_results(original, recheck)
    assert result.mismatches == 1
    assert result.discrepancy_rate > 0


import json
import pytest


def _make_adjudication_mock(decisions: dict[str, bool]):
    """创建一个模拟的 call_llm_fn，根据 decisions 表返回裁决结果。"""
    async def mock_fn(prompt: str) -> str:
        rulings = []
        for conv_id, violates in decisions.items():
            rulings.append({"id": conv_id, "violates": violates})
        return json.dumps({"rulings": rulings})
    return mock_fn


@pytest.mark.anyio
async def test_adjudicate_no_disagreement():
    """当所有争议都裁决与 original 一致时，qc_results 不变。"""
    from auto_qc.framework.cross_validator import adjudicate
    original = [
        {"id": "1", "violations": [{"rule_id": "R01"}]},
        {"id": "2", "violations": []},
    ]
    recheck = [
        {"id": "1", "violations": []},
        {"id": "2", "violations": [{"rule_id": "R01"}]},
    ]
    qc_results = [
        {"id": "1", "status": "violation", "violations": [{"rule_id": "R01", "rule_name": "t", "severity": "高", "evidence": "e", "suggestion": "s"}]},
        {"id": "2", "status": "pass", "violations": []},
    ]
    mock = _make_adjudication_mock({})
    result, _ = await adjudicate(
        original, recheck, qc_results, "R01", mock,
        {"1": "对话1", "2": "对话2"},
    )
    assert len(result) == 2


@pytest.mark.anyio
async def test_adjudicate_fixes_disagreement():
    """当第三次裁决与 original 不一致时，应修正 qc_results。"""
    from auto_qc.framework.cross_validator import adjudicate
    original = [
        {"id": "1", "violations": [{"rule_id": "R01"}]},
        {"id": "2", "violations": []},
    ]
    recheck = [
        {"id": "1", "violations": []},
        {"id": "2", "violations": [{"rule_id": "R01"}]},
    ]
    qc_results = [
        {"id": "1", "status": "violation", "violations": [{"rule_id": "R01", "rule_name": "t", "severity": "高", "evidence": "e", "suggestion": "s"}]},
        {"id": "2", "status": "pass", "violations": []},
    ]
    # 第三次裁决：1 通过（不违规），2 违规
    mock = _make_adjudication_mock({"1": False, "2": True})
    result, _ = await adjudicate(
        original, recheck, qc_results, "R01", mock,
        {"1": "对话1", "2": "对话2"},
    )
    result_map = {r["id"]: r for r in result}
    # 1 原来有 R01 违规，应被移除
    assert not any(v["rule_id"] == "R01" for v in result_map["1"]["violations"])
    # 2 原来无违规，应被添加 R01
    assert any(v["rule_id"] == "R01" for v in result_map["2"]["violations"])
