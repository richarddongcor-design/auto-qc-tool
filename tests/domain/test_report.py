import tempfile
from pathlib import Path
from auto_qc.domain.report import write_report, verify_report_exists


def test_write_basic_report():
    qc_results = [
        {"id": "1", "time": "2024-01-01", "intent": "A",
         "violations": [{"rule_id": "R01", "rule_name": "测试", "severity": "高",
                         "evidence": "xxx", "suggestion": "yyy"}]},
        {"id": "2", "time": "2024-01-02", "intent": "B", "violations": []},
    ]
    stats = {"total": 2, "pass": 1, "violation_rate": "50.0%",
             "rules_hit": {"R01": 1}, "rule_names": {"R01": "测试规则"}}

    with tempfile.TemporaryDirectory() as tmpdir:
        output = str(Path(tmpdir) / "报告.xlsx")
        write_report(output, qc_results, stats)
        assert verify_report_exists(output)


def test_report_file_not_exists():
    assert not verify_report_exists("/nonexistent/path/report.xlsx")
