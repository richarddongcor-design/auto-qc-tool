"""历史记录查询。"""
import json
from pathlib import Path


def get_recent_qc_runs(limit: int = 10) -> list[dict]:
    """扫描 output/ 目录获取最近的质检运行记录。

    质检运行目录以 report.xlsx 为标志，同时需要 summary.json。
    """
    output_dir = Path("output")
    if not output_dir.exists():
        return []

    runs = []
    for d in sorted(output_dir.iterdir(), key=lambda p: p.name, reverse=True):
        if not d.is_dir():
            continue
        summary_file = d / "summary.json"
        if summary_file.exists():
            try:
                summary = json.loads(summary_file.read_text(encoding="utf-8"))
                # QC runs have report.xlsx; PI runs have a domain field
                is_qc = (d / "report.xlsx").exists() or "rule_sets" in summary
                if not is_qc and not summary.get("domain"):
                    continue  # skip unknown entries
                runs.append({
                    "id": d.name,
                    "data_file": summary.get("data_file", ""),
                    "violation_rate": summary.get("violation_rate", ""),
                    "total": summary.get("total_conversations", 0),
                    "status": summary.get("status", "completed"),
                })
            except (json.JSONDecodeError, OSError):
                continue
        if len(runs) >= limit:
            break
    return runs


def get_recent_pi_runs(limit: int = 10) -> list[dict]:
    """扫描 output/ 目录获取最近的问题挖掘运行记录。"""
    output_dir = Path("output")
    if not output_dir.exists():
        return []

    runs = []
    for d in sorted(output_dir.iterdir(), key=lambda p: p.name, reverse=True):
        if not d.is_dir():
            continue
        summary_file = d / "summary.json"
        if summary_file.exists():
            try:
                summary = json.loads(summary_file.read_text(encoding="utf-8"))
                if not summary.get("domain"):
                    continue
                runs.append({
                    "id": d.name,
                    "data_file": summary.get("data_file", ""),
                    "domain": summary.get("domain", ""),
                    "status": summary.get("status", ""),
                    "run_id": summary.get("run_id", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue
        if len(runs) >= limit:
            break
    return runs
