# auto-qc Skill 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个完全可移植的 Claude Code skill（~/.agents/skills/auto-qc/），用于大规模外呼通话对话文本质检。支持合规检测（用户提供规则）和归因分析（内置），输出 Excel 质检报告。

**架构:** 统一框架 + 两套规则。Python 负责 I/O（Excel 读写、预处理、批次拆分），LLM 负责所有质检判断（sub-agent Worker 逐批打标）。Harness 范式：拆分→分发→收集→校验→汇总。

**Tech Stack:** Python (openpyxl, pandas, json-repair), Claude Code Agent tool, Markdown 规则文件

---

## 文件总览

**新建文件（全部在 ~/.agents/skills/auto-qc/ 下）：**
- `SKILL.md` — 主入口，引导 Claude 当 Supervisor
- `templates/worker-prompt.md` — Worker 批处理打标模板
- `templates/attribution-prompt.md` — 归因分析 Worker 模板
- `templates/attribution-rules.md` — 内置归因规则（5 Why 根因分析）
- `scripts/data_loader.py` — Excel 读取、列匹配、对话预处理、批次拆分、进度管理
- `scripts/report_writer.py` — 报告 Excel 生成、临时文件清理
- `requirements.txt` — Python 依赖声明

---

### Task 1: 创建 Skill 骨架 + requirements.txt

**Files:**
- Create: `~/.agents/skills/auto-qc/requirements.txt`
- Create: `~/.agents/skills/auto-qc/templates/` (目录)
- Create: `~/.agents/skills/auto-qc/scripts/` (目录)

- [ ] **Step 1: 创建目录结构和 requirements.txt**

运行以下命令创建目录：

```bash
mkdir -p ~/.agents/skills/auto-qc/templates
mkdir -p ~/.agents/skills/auto-qc/scripts
```

创建 `~/.agents/skills/auto-qc/requirements.txt`：

```
openpyxl==3.1.5
pandas==2.2.3
json-repair==0.44.3
```

- [ ] **Step 2: 安装依赖验证**

```bash
cd ~/.agents/skills/auto-qc && uv pip install -r requirements.txt
```

Expected: 三个包安装成功，无报错。

- [ ] **Step 3: Commit**

```bash
cd ~/.agents/skills && git add auto-qc/ && git commit -m "chore: 创建 auto-qc skill 骨架和依赖"
```

---

### Task 2: data_loader.py — Excel 读取 + 列匹配 + 对话预处理

**Files:**
- Create: `~/.agents/skills/auto-qc/scripts/data_loader.py`

这个脚本是 skill 的"搬运工"，负责所有 Python 侧的数据操作。提供三个子命令：
1. `load` — 读取 Excel、匹配列名、预处理对话、输出批次 JSON
2. `resume` — 读取进度文件，返回未完成的批次列表
3. `save_progress` — 更新进度文件

- [ ] **Step 1: 编写测试**

创建临时测试文件 `~/.agents/skills/auto-qc/scripts/test_data_loader.py`：

```python
"""测试 data_loader.py 的列匹配和预处理功能"""
import json
import tempfile
from pathlib import Path

from data_loader import match_columns, preprocess_conversation, load_excel


def test_match_columns_exact():
    """测试精确列名匹配"""
    headers = ["id", "时间", "对话文本", "意向结果"]
    result = match_columns(headers)
    assert result["id_col"] == "id"
    assert result["intent_col"] == "意向结果"


def test_match_columns_fuzzy():
    """测试模糊列名匹配（关键词）"""
    headers = ["call_id", "通话时间", "conversation", "intent_result"]
    result = match_columns(headers)
    assert result["id_col"] == "call_id"
    assert result["time_col"] == "通话时间"
    assert result["conv_col"] == "conversation"
    assert result["intent_col"] == "intent_result"


def test_match_columns_missing():
    """测试缺失列时报错"""
    headers = ["id", "时间"]
    try:
        match_columns(headers)
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "对话" in str(e)


def test_preprocess_conversation():
    """测试对话 JSON → 可读文本转换"""
    conv_json = [
        {"ttsResult": "你好，请问是张三吗？", "asrResult": "用户无应答"},
        {"ttsResult": "您好，我是猎聘这边的...", "asrResult": "是的哪位？"}
    ]
    result = preprocess_conversation(conv_json)
    assert "AI: 你好，请问是张三吗？" in result
    assert "用户: 用户无应答" in result
    assert "AI: 您好，我是猎聘这边的..." in result
    assert "用户: 是的哪位？" in result


def test_load_excel_and_batch(tmp_path):
    """测试完整加载+拆分流程"""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["id", "时间", "对话文本", "意向结果"])
    for i in range(1, 251):  # 250 条
        conv = json.dumps([
            {"ttsResult": f"你好 {i}", "asrResult": "嗯"}
        ], ensure_ascii=False)
        ws.append([i, "2026-06-01 10:00:00", conv, "A(有意向)"])
    
    test_xlsx = str(tmp_path / "test.xlsx")
    wb.save(test_xlsx)

    result = load_excel(test_xlsx, batch_size=100)
    assert result["total"] == 250
    assert result["num_batches"] == 3  # 250 / 100 = 3 批
    assert len(result["batches"][0]) == 100
    assert len(result["batches"][2]) == 50  # 最后一批
```

- [ ] **Step 2: 编写 data_loader.py 实现**

```python
"""
data_loader.py — Excel 读取、列匹配、对话预处理、批次拆分、进度管理

通过子命令调用：
  python data_loader.py load --data <excel路径> --batch-size <int> --output <输出目录>
  python data_loader.py resume --data <excel路径> --output <输出目录>
  python data_loader.py save_progress --data <excel路径> --progress <progress.json路径>
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import openpyxl
import pandas as pd


# ─── 列名映射 ───

COLUMN_PATTERNS = {
    "id_col": ["id", "通话ID", "call_id", "callId", "通话id"],
    "time_col": ["时间", "通话时间", "call_time", "callTime", "通话日期"],
    "conv_col": ["对话", "对话文本", "conversation", "conv", "通话内容", "对话内容"],
    "intent_col": ["意向", "意向结果", "intent_result", "intentResult", "结果"],
}


def match_columns(headers: list[str]) -> dict[str, str]:
    """按关键词匹配 Excel 列名，返回映射字典。匹配失败时抛出 ValueError。"""
    result = {}
    lower_headers = {h.strip().lower(): h for h in headers}

    for key, keywords in COLUMN_PATTERNS.items():
        matched = None
        # 精确匹配（忽略大小写）
        for kw in keywords:
            if kw.lower() in lower_headers:
                matched = lower_headers[kw.lower()]
                break
        # 模糊匹配：关键词在列名中
        if matched is None:
            for h in headers:
                for kw in keywords:
                    if kw.lower() in h.lower():
                        matched = h
                        break
                if matched:
                    break
        if matched is None:
            raise ValueError(
                f"未找到 {key} 对应的列。当前表头: {headers}。"
                f"期望包含以下关键词之一: {keywords}"
            )
        result[key] = matched

    return result


# ─── 对话预处理 ───

def preprocess_conversation(conv_json: list[dict]) -> str:
    """将 TTS/ASR JSON 转为 'AI: xxx / 用户: xxx' 的可读对话文本。"""
    lines = []
    for turn in conv_json:
        tts = turn.get("ttsResult", "").strip()
        asr = turn.get("asrResult", "").strip()
        if tts:
            lines.append(f"AI: {tts}")
        if asr:
            lines.append(f"用户: {asr}")
    return "\n".join(lines)


def preprocess_conversations(conv_raw: str) -> str:
    """处理原始单元格字符串（可能双重编码的 JSON）。"""
    # 去掉外层引号（Excel 有时会把 JSON 字符串再包一层引号）
    if conv_raw.startswith('"['):
        try:
            conv_raw = json.loads(conv_raw)
        except json.JSONDecodeError:
            pass

    if isinstance(conv_raw, str):
        conv_data = json.loads(conv_raw)
    else:
        conv_data = conv_raw

    return preprocess_conversation(conv_data)


# ─── Excel 加载 + 批次拆分 ───

def load_excel(
    data_path: str,
    batch_size: int = 100,
    filter_intent: Optional[str] = None,
    exclude_intent: Optional[str] = None,
) -> dict[str, Any]:
    """
    读取 Excel，预处理对话，拆分为批次。

    Args:
        data_path: Excel 文件路径
        batch_size: 每批对话数量
        filter_intent: 如果指定，只保留该意向结果的对话
        exclude_intent: 如果指定，排除该意向结果的对话

    Returns:
        {
            "total": int,
            "num_batches": int,
            "batches": [
                [
                    {"id": "...", "time": "...", "intent": "...", "conversation": "AI: ...\n用户: ..."},
                    ...
                ],
                ...
            ],
        }
    """
    wb = openpyxl.load_workbook(data_path, read_only=True, data_only=True)
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    headers = next(rows_iter)
    col_map = match_columns(list(headers))

    id_idx = headers.index(col_map["id_col"])
    time_idx = headers.index(col_map["time_col"])
    conv_idx = headers.index(col_map["conv_col"])
    intent_idx = headers.index(col_map["intent_col"])

    conversations = []
    for row in rows_iter:
        if row[id_idx] is None:
            continue

        intent = str(row[intent_idx]).strip() if row[intent_idx] else ""

        if filter_intent and intent != filter_intent:
            continue

        if exclude_intent and intent == exclude_intent:
            continue

        try:
            conv_text = preprocess_conversations(str(row[conv_idx]))
        except (json.JSONDecodeError, TypeError):
            conv_text = "[对话解析失败]"

        conversations.append({
            "id": str(row[id_idx]),
            "time": str(row[time_idx]).strip() if row[time_idx] else "",
            "intent": intent,
            "conversation": conv_text,
        })

    wb.close()

    # 拆分批次
    batches = []
    for i in range(0, len(conversations), batch_size):
        batches.append(conversations[i:i + batch_size])

    return {
        "total": len(conversations),
        "num_batches": len(batches),
        "batches": batches,
    }


# ─── 进度管理 ───

def load_progress(progress_path: str) -> dict[str, Any]:
    """读取进度文件。不存在时返回空结构。"""
    if os.path.exists(progress_path):
        with open(progress_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "total_batches": 0,
        "completed_batches": 0,
        "batch_status": {},
        "completed_ids": [],
        "failed_batches": [],
        "status": "not_started",
    }


def save_progress(progress_path: str, progress: dict[str, Any]) -> None:
    """写入进度文件。"""
    progress["updated_at"] = datetime.now().isoformat()
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


# ─── CLI 入口 ───

def main():
    parser = argparse.ArgumentParser(description="auto-qc 数据加载器")
    sub = parser.add_subparsers(dest="command", required=True)

    # load 子命令
    load_p = sub.add_parser("load", help="读取 Excel、预处理、拆分批次")
    load_p.add_argument("--data", required=True, help="Excel 文件路径")
    load_p.add_argument("--batch-size", type=int, default=100, help="每批数量")
    load_p.add_argument("--output", required=True, help="批次 JSON 输出目录")
    load_p.add_argument("--filter-intent", help="过滤特定意向结果（保留）")
    load_p.add_argument("--exclude-intent", help="排除特定意向结果（过滤掉）")

    # resume 子命令
    resume_p = sub.add_parser("resume", help="读取进度，返回未完成批次")
    resume_p.add_argument("--data", required=True, help="Excel 文件路径")
    resume_p.add_argument("--output", required=True, help="批次 JSON 输出目录")
    resume_p.add_argument("--progress", required=True, help="进度文件路径")
    resume_p.add_argument("--batch-size", type=int, default=100, help="每批数量")
    resume_p.add_argument("--filter-intent", help="过滤特定意向结果（保留）")
    resume_p.add_argument("--exclude-intent", help="排除特定意向结果（过滤掉）")

    # save_progress 子命令
    save_p = sub.add_parser("save_progress", help="更新进度文件")
    save_p.add_argument("--progress", required=True, help="进度文件路径")
    save_p.add_argument("--batch-id", required=True, help="完成的批次 ID")
    save_p.add_argument("--result-file", required=True, help="该批次的结果 JSON 路径")

    args = parser.parse_args()

    if args.command == "load":
        result = load_excel(args.data, args.batch_size, args.filter_intent)
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)

        for i, batch in enumerate(result["batches"]):
            batch_file = output_dir / f"batch_{i+1}.json"
            with open(batch_file, "w", encoding="utf-8") as f:
                json.dump({
                    "batch_id": i + 1,
                    "total": len(batch),
                    "conversations": batch,
                }, f, ensure_ascii=False, indent=2)

        print(json.dumps({
            "total": result["total"],
            "num_batches": result["num_batches"],
            "output_dir": str(output_dir),
        }, ensure_ascii=False))

    elif args.command == "resume":
        progress = load_progress(args.progress)

        if progress["status"] == "not_started":
            # 首次运行，加载全部
            result = load_excel(args.data, args.batch_size, args.filter_intent, getattr(args, 'exclude_intent', None))
            output_dir = Path(args.output)
            output_dir.mkdir(parents=True, exist_ok=True)

            for i, batch in enumerate(result["batches"]):
                batch_file = output_dir / f"batch_{i+1}.json"
                with open(batch_file, "w", encoding="utf-8") as f:
                    json.dump({
                        "batch_id": i + 1,
                        "total": len(batch),
                        "conversations": batch,
                    }, f, ensure_ascii=False, indent=2)

            progress["total_batches"] = result["num_batches"]
            save_progress(args.progress, progress)

            print(json.dumps({
                "action": "full_load",
                "total": result["total"],
                "num_batches": result["num_batches"],
                "pending_batches": list(range(1, result["num_batches"] + 1)),
            }, ensure_ascii=False))
        else:
            # 检查哪些批次未完成
            pending = []
            for i in range(1, progress["total_batches"] + 1):
                if progress["batch_status"].get(str(i)) != "done":
                    pending.append(i)

            print(json.dumps({
                "action": "resume",
                "completed_batches": progress["completed_batches"],
                "total_batches": progress["total_batches"],
                "pending_batches": pending,
            }, ensure_ascii=False))

    elif args.command == "save_progress":
        progress = load_progress(args.progress)
        batch_id = args.batch_id

        progress["batch_status"][batch_id] = "done"
        progress["completed_batches"] = sum(
            1 for v in progress["batch_status"].values() if v == "done"
        )

        # 读取该批次结果 ID
        if os.path.exists(args.result_file):
            with open(args.result_file, "r", encoding="utf-8") as f:
                batch_result = json.load(f)
            for r in batch_result.get("results", []):
                progress["completed_ids"].append(r.get("id"))

        if progress["completed_batches"] >= progress["total_batches"]:
            progress["status"] = "done"

        save_progress(args.progress, progress)
        print(f"Progress saved: batch {batch_id} done ({progress['completed_batches']}/{progress['total_batches']})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 运行测试验证**

```bash
cd ~/.agents/skills/auto-qc/scripts && python -m pytest test_data_loader.py -v
```

Expected: 5 个测试全部通过。

- [ ] **Step 4: Commit**

```bash
cd ~/.agents/skills && git add auto-qc/ && git commit -m "feat: 添加 data_loader.py（Excel 读取、预处理、批次拆分、进度管理）"
```

---

### Task 3: report_writer.py — 报告 Excel 输出 + 临时文件清理

**Files:**
- Create: `~/.agents/skills/auto-qc/scripts/report_writer.py`
- Create: `~/.agents/skills/auto-qc/scripts/test_report_writer.py`

- [ ] **Step 1: 编写测试**

```python
"""测试 report_writer.py 的报告生成和清理功能"""
import json
import tempfile
from pathlib import Path

from report_writer import write_report, cleanup_temp_files


def test_write_report_basic(tmp_path):
    """测试基本报告生成"""
    qc_results = [
        {"id": "1", "time": "2026-06-01", "intent": "A(有意向)", "violations": []},
        {
            "id": "2",
            "time": "2026-06-01",
            "intent": "B(不确定)",
            "violations": [
                {
                    "rule_id": "R01",
                    "rule_name": "无视用户明确拒绝",
                    "severity": "高",
                    "evidence": "用户说不考虑，AI继续...",
                    "suggestion": "应礼貌结束",
                }
            ],
        },
    ]
    attribution_results = {
        "B(不确定)": [
            {
                "category": "未介绍岗位亮点",
                "count": 5,
                "ratio": 0.20,
                "examples": ["对话 123..."],
                "suggestion": "应主动介绍薪资地点",
            }
        ]
    }
    stats = {"total": 2, "violation_rate": 0.5, "rules_hit": {"R01": 1}}

    output_path = str(tmp_path / "report.xlsx")
    write_report(output_path, qc_results, attribution_results, stats)

    assert Path(output_path).exists()

    # 验证 sheet 存在
    import openpyxl
    wb = openpyxl.load_workbook(output_path)
    assert "合规检测" in wb.sheetnames
    assert "归因分析" in wb.sheetnames
    assert "统计概览" in wb.sheetnames
    wb.close()


def test_cleanup_temp_files(tmp_path):
    """测试临时文件清理"""
    (tmp_path / "progress.json").write_text("{}")
    (tmp_path / "failed_batches.json").write_text("{}")
    (tmp_path / "batches" / "batch_1.json").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "batches" / "batch_1.json").write_text("{}")

    cleanup_temp_files(str(tmp_path))

    assert not (tmp_path / "progress.json").exists()
    assert not (tmp_path / "failed_batches.json").exists()
    assert not (tmp_path / "batches").exists()
```

- [ ] **Step 2: 编写 report_writer.py 实现**

```python
"""
report_writer.py — 质检报告 Excel 生成 + 临时文件清理

子命令：
  python report_writer.py write --output <报告路径> --qc-results <JSON路径> --attribution <JSON路径>
  python report_writer.py cleanup --dir <目录路径> [--keep-temp]
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment


# ─── 样式常量 ───

HEADER_FONT = Font(name="Microsoft YaHei", bold=True, size=11, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
SEVERITY_FILLS = {
    "高": PatternFill(start_color="FF6B6B", end_color="FF6B6B", fill_type="solid"),
    "中": PatternFill(start_color="FFD93D", end_color="FFD93D", fill_type="solid"),
    "低": PatternFill(start_color="6BCB77", end_color="6BCB77", fill_type="solid"),
}
NORMAL_ALIGNMENT = Alignment(wrap_text=True, vertical="top")


def _style_header(cell):
    """设置表头样式。"""
    cell.font = HEADER_FONT
    cell.fill = HEADER_FILL
    cell.alignment = Alignment(horizontal="center", vertical="center")


def _style_row(row, severity=None):
    """设置数据行样式。"""
    for cell in row:
        cell.alignment = NORMAL_ALIGNMENT
    if severity and severity in SEVERITY_FILLS:
        # 给"危害程度"列上色
        for cell in row:
            if cell.value == severity:
                cell.fill = SEVERITY_FILLS[severity]


# ─── 报告生成 ───

def write_report(
    output_path: str,
    qc_results: list[dict],
    attribution_results: dict[str, list[dict]],
    stats: dict,
) -> None:
    """
    生成质检报告 Excel，包含三个 sheet。

    Args:
        output_path: 输出文件路径
        qc_results: 合规检测结果列表
        attribution_results: 归因分析结果 {意向结果: [归因条目]}
        stats: 统计概览数据
    """
    wb = openpyxl.Workbook()

    # Sheet 1: 合规检测
    ws_qc = wb.active
    ws_qc.title = "合规检测"
    ws_qc.sheet_properties.tabColor = "4472C4"

    headers = ["id", "时间", "意向结果", "违规规则", "问题类型", "危害程度", "证据片段", "改进建议"]
    for col, h in enumerate(headers, 1):
        _style_header(ws_qc.cell(1, col, h))

    row_num = 2
    for record in qc_results:
        dialog_id = record.get("id", "")
        time_val = record.get("time", "")
        intent = record.get("intent", "")
        violations = record.get("violations", [])

        if not violations:
            # 无违规：一行标记"通过"
            ws_qc.cell(row_num, 1, dialog_id)
            ws_qc.cell(row_num, 2, time_val)
            ws_qc.cell(row_num, 3, intent)
            ws_qc.cell(row_num, 4, "通过")
            row_num += 1
        else:
            for v in violations:
                ws_qc.cell(row_num, 1, dialog_id)
                ws_qc.cell(row_num, 2, time_val)
                ws_qc.cell(row_num, 3, intent)
                ws_qc.cell(row_num, 4, v.get("rule_id", ""))
                ws_qc.cell(row_num, 5, v.get("rule_name", ""))
                ws_qc.cell(row_num, 6, v.get("severity", ""))
                ws_qc.cell(row_num, 7, v.get("evidence", ""))
                ws_qc.cell(row_num, 8, v.get("suggestion", ""))
                _style_row(ws_qc[row_num - 1], v.get("severity"))
                row_num += 1

    # 自适应列宽
    for col in ws_qc.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        ws_qc.column_dimensions[col[0].column_letter].width = min(max_len + 2, 50)

    # Sheet 2: 归因分析
    ws_attr = wb.create_sheet("归因分析")
    ws_attr.sheet_properties.tabColor = "6BCB77"

    attr_headers = ["意向结果", "归因类别", "占比", "数量", "典型案例", "改进建议"]
    for col, h in enumerate(attr_headers, 1):
        _style_header(ws_attr.cell(1, col, h))

    row_num = 2
    for intent, categories in attribution_results.items():
        for cat in categories:
            ws_attr.cell(row_num, 1, intent)
            ws_attr.cell(row_num, 2, cat.get("category", ""))
            ws_attr.cell(row_num, 3, f"{cat.get('ratio', 0) * 100:.1f}%")
            ws_attr.cell(row_num, 4, cat.get("count", 0))
            ws_attr.cell(row_num, 5, "\n".join(cat.get("examples", [])[:3]))
            ws_attr.cell(row_num, 6, cat.get("suggestion", ""))
            row_num += 1

    for col in ws_attr.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        ws_attr.column_dimensions[col[0].column_letter].width = min(max_len + 2, 50)

    # Sheet 3: 统计概览
    ws_stats = wb.create_sheet("统计概览")
    ws_stats.sheet_properties.tabColor = "FFD93D"

    ws_stats.cell(1, 1, "指标")
    ws_stats.cell(1, 2, "数值")
    _style_header(ws_stats.cell(1, 1))
    _style_header(ws_stats.cell(1, 2))

    row_num = 2
    for key, val in stats.items():
        ws_stats.cell(row_num, 1, key)
        ws_stats.cell(row_num, 2, val)
        row_num += 1

    # 保存
    wb.save(output_path)
    wb.close()


# ─── 临时文件清理 ───

def cleanup_temp_files(work_dir: str, keep_temp: bool = False) -> None:
    """
    清理处理过程中的临时文件。

    Args:
        work_dir: 工作目录（批次 JSON、进度文件所在目录）
        keep_temp: 是否保留临时文件
    """
    if keep_temp:
        print("保留临时文件 (--keep-temp)")
        return

    work = Path(work_dir)

    # 清理进度文件
    for name in ["progress.json", "failed_batches.json"]:
        f = work / name
        if f.exists():
            f.unlink()
            print(f"已删除临时文件: {f}")

    # 清理批次目录
    batches_dir = work / "batches"
    if batches_dir.exists():
        shutil.rmtree(batches_dir)
        print(f"已删除批次目录: {batches_dir}")

    # 清理中间结果文件
    for f in work.glob("batch_result_*.json"):
        f.unlink()
        print(f"已删除中间结果: {f}")


# ─── CLI 入口 ───

def main():
    parser = argparse.ArgumentParser(description="auto-qc 报告生成器")
    sub = parser.add_subparsers(dest="command", required=True)

    write_p = sub.add_parser("write", help="生成质检报告 Excel")
    write_p.add_argument("--output", required=True, help="报告输出路径")
    write_p.add_argument("--qc-results", required=True, help="合规检测结果 JSON 路径")
    write_p.add_argument("--attribution", help="归因分析结果 JSON 路径")
    write_p.add_argument("--stats", help="统计概览数据 JSON 路径")

    cleanup_p = sub.add_parser("cleanup", help="清理临时文件")
    cleanup_p.add_argument("--dir", required=True, help="工作目录")
    cleanup_p.add_argument("--keep-temp", action="store_true", help="保留临时文件")

    args = parser.parse_args()

    if args.command == "write":
        with open(args.qc_results, "r", encoding="utf-8") as f:
            qc_data = json.load(f)

        attr_data = {}
        if args.attribution and os.path.exists(args.attribution):
            with open(args.attribution, "r", encoding="utf-8") as f:
                attr_data = json.load(f)

        stats = {}
        if args.stats and os.path.exists(args.stats):
            with open(args.stats, "r", encoding="utf-8") as f:
                stats = json.load(f)

        # 确保输出目录存在
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)

        write_report(args.output, qc_data, attr_data, stats)
        print(f"报告已生成: {args.output}")

    elif args.command == "cleanup":
        cleanup_temp_files(args.dir, args.keep_temp)
        print("临时文件清理完成")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 运行测试验证**

```bash
cd ~/.agents/skills/auto-qc/scripts && python -m pytest test_report_writer.py -v
```

Expected: 2 个测试全部通过。

- [ ] **Step 4: Commit**

```bash
cd ~/.agents/skills && git add auto-qc/ && git commit -m "feat: 添加 report_writer.py（报告生成、临时文件清理）"
```

---

### Task 4: rules_parser.py — Markdown 规则解析器

**Files:**
- Create: `~/.agents/skills/auto-qc/scripts/rules_parser.py`
- Create: `~/.agents/skills/auto-qc/scripts/test_rules_parser.py`

这个模块解析 rules.md 格式的规则文件，输出 JSON 规则包。

- [ ] **Step 1: 编写测试**

```python
"""测试 rules_parser.py 的规则解析功能"""
import json
from rules_parser import parse_rules


def test_parse_single_rule():
    """测试解析单条规则"""
    text = """## R01: 无视用户明确拒绝

**严重程度**: 高
**发现次数**: 558/25600 (2.2%)

**描述**: 用户明确表示不考虑后，AI 未礼貌结束对话。

**检测逻辑**: 检测用户发言包含明确拒绝关键词后，AI 下一条发言是否继续推进。

**典型案例**:
- 对话 11237419617 (轮次 4): 用户: 嗯，暂时不考虑了啊，谢谢 | AI继续: ...
- 对话 11149319380 (轮次 4): 用户: 啊，不考虑，拜拜 | AI继续: ...
"""
    rules = parse_rules(text)
    assert len(rules) == 1
    assert rules[0]["rule_id"] == "R01"
    assert rules[0]["name"] == "无视用户明确拒绝"
    assert rules[0]["severity"] == "高"
    assert "拒绝" in rules[0]["description"]
    assert len(rules[0]["examples"]) == 2


def test_parse_multiple_rules():
    """测试解析多条规则"""
    text = """## R01: 规则一

**严重程度**: 高
**发现次数**: 100

**描述**: 描述一

**检测逻辑**: 逻辑一

**典型案例**:
- 案例1

## R02: 规则二

**严重程度**: 中
**发现次数**: 50

**描述**: 描述二

**检测逻辑**: 逻辑二

**典型案例**:
- 案例2
"""
    rules = parse_rules(text)
    assert len(rules) == 2
    assert rules[0]["rule_id"] == "R01"
    assert rules[1]["rule_id"] == "R02"
    assert rules[0]["severity"] == "高"
    assert rules[1]["severity"] == "中"


def test_parse_rules_to_package():
    """测试输出规则包 JSON"""
    text = """## R01: 测试规则

**严重程度**: 低
**发现次数**: 10

**描述**: 这是一条测试规则

**检测逻辑**: 测试用

**典型案例**:
- 案例1
"""
    rules = parse_rules(text)
    package = {"rules": rules}
    assert "rules" in package
    assert package["rules"][0]["rule_id"] == "R01"
```

- [ ] **Step 2: 编写 rules_parser.py 实现**

```python
"""
rules_parser.py — Markdown 规则文件解析器

将 rules.md 格式的规则解析为 JSON 规则包。

调用方式：
  python rules_parser.py --rules <规则文件路径> --output <输出JSON路径>
  python rules_parser.py --rules <规则文件路径> --stdout
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional


def parse_rules(markdown_text: str) -> list[dict]:
    """
    解析 Markdown 规则文本，返回结构化规则列表。

    支持的格式：
    ## R01: 规则名称

    **严重程度**: 高/中/低
    **发现次数**: 数字

    **描述**: 描述文本

    **检测逻辑**: 条件描述

    **典型案例**:
    - 案例1
    - 案例2
    """
    rules = []

    # 按 "## R" 分割规则块
    blocks = re.split(r"\n## (R\d+:\s*)", markdown_text)
    # blocks 格式: [前缀, "R01: ", 内容, "R02: ", 内容, ...]

    i = 1
    while i < len(blocks) - 1:
        rule_prefix = blocks[i].strip()  # "R01: "
        content = blocks[i + 1]

        # 提取 rule_id 和 name
        match = re.match(r"(R\d+):\s*(.+)", rule_prefix)
        if not match:
            i += 2
            continue

        rule_id = match.group(1)
        name = match.group(2).strip()

        # 提取严重程度
        severity_match = re.search(r"\*\*严重程度\*\*[:：]\s*(.+)", content)
        severity = severity_match.group(1).strip() if severity_match else ""

        # 提取描述
        desc_match = re.search(r"\*\*描述\*\*[:：]\s*(.+?)(?=\n\*\*|\n##|$)", content, re.DOTALL)
        description = desc_match.group(1).strip() if desc_match else ""

        # 提取检测逻辑
        logic_match = re.search(r"\*\*检测逻辑\*\*[:：]\s*(.+?)(?=\n\*\*|\n##|$)", content, re.DOTALL)
        detection_logic = logic_match.group(1).strip() if logic_match else ""

        # 提取典型案例
        examples = []
        examples_match = re.search(r"\*\*典型案例\*\*[:：]\s*\n?(.+?)(?=\n\*\*|\n##|$)", content, re.DOTALL)
        if examples_match:
            examples_text = examples_match.group(1).strip()
            examples = [
                line.strip().lstrip("- ").strip()
                for line in examples_text.split("\n")
                if line.strip().startswith("-")
            ]

        rules.append({
            "rule_id": rule_id,
            "name": name,
            "severity": severity,
            "description": description,
            "detection_logic": detection_logic,
            "examples": examples,
        })

        i += 2

    return rules


def main():
    parser = argparse.ArgumentParser(description="规则解析器")
    parser.add_argument("--rules", required=True, help="规则文件路径")
    parser.add_argument("--output", help="输出 JSON 路径（可选，默认 stdout）")
    parser.add_argument("--stdout", action="store_true", help="输出到标准输出")

    args = parser.parse_args()

    rules_text = Path(args.rules).read_text(encoding="utf-8")
    rules = parse_rules(rules_text)
    package = {"rules": rules}

    output_json = json.dumps(package, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output_json, encoding="utf-8")
        print(f"规则包已保存: {args.output}")
    else:
        print(output_json)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 运行测试验证**

```bash
cd ~/.agents/skills/auto-qc/scripts && python -m pytest test_rules_parser.py -v
```

Expected: 3 个测试全部通过。

- [ ] **Step 4: Commit**

```bash
cd ~/.agents/skills && git add auto-qc/ && git commit -m "feat: 添加 rules_parser.py（Markdown 规则解析器）"
```

---

### Task 5: 内置归因规则 — attribution-rules.md

**Files:**
- Create: `~/.agents/skills/auto-qc/templates/attribution-rules.md`

- [ ] **Step 1: 创建内置归因规则**

```markdown
# 归因分析规则

生成时间: 2026-06-01
用途: 分析非"有意向"对话中，什么因素导致对话没有走向"有意向"

分析方式：对每条对话，逐步追问"为什么没谈成？"，找到根因层级即可（不强制追问到第 5 层）。

---

## A01: AI 未介绍岗位关键信息

**描述**: AI 在对话中未提及或未清晰说明岗位的核心信息（薪资、工作地点、公司名称），导致用户无法判断机会吸引力。

**检测信号**: 对话全程未出现具体薪资数字、未提及公司全称、工作地点模糊或未说明。

**典型案例**:
- 用户反复询问"具体做什么产品"，AI 始终未给出明确回答
- 对话结束时用户仍不清楚薪资和工作地点

---

## A02: 未识别用户兴趣点，未做针对性引导

**描述**: 用户表达了对某方面兴趣（如"这个岗位有晋升空间吗"），但 AI 未捕捉到该兴趣点，仍然用模板化话术推进。

**检测信号**: 用户提出具体关注点后，AI 未在该方向上做进一步介绍，而是跳转到要简历话术。

**典型案例**:
- 用户问"这个岗位的技术栈是什么"，AI 回答"您可以晚点看下岗位细节"
- 用户表示对某行业感兴趣，AI 未做延伸介绍

---

## A03: 回复过于模板化/机械

**描述**: AI 回复缺乏人情味，大量使用固定话术模板，用户感受到"机器感"而非与真人顾问对话。

**检测信号**: AI 多次输出几乎相同的长段落话术；缺乏对用户个性化问题的回应；语气僵硬。

**典型案例**:
- 用户提问后，AI 回复一段与问题无关的标准岗位介绍
- 连续轮次 AI 输出内容高度重复

---

## A04: 未正面回答用户追问

**描述**: 用户提出具体追问（产品、岗位性质、薪资、业务方向等）时，AI 用"您可以看下猎聘"、"让HR联系您"等话术搪塞，未给出任何实质性回答。

**检测信号**: 用户发言包含疑问词（什么、怎么、哪里、具体），但 AI 未给出具体信息。

**典型案例**:
- 用户问"这个岗位做什么的"，AI 答"您可以晚点详细看下具体岗位细节"
- 用户问"薪资是多少"，AI 答"企业HR会再联系您详细介绍"

---

## A05: 节奏把控不当

**描述**: 对话节奏不合理。推进过快（用户还没了解岗位就要简历），或过慢（大量无效轮次浪费用户时间）。

**检测信号**: 用户刚表态就立刻要简历（过快）；或同一信息反复确认超过 3 轮（过慢）。

**典型案例**:
- AI 介绍完公司名立刻要简历，用户还未表示任何兴趣
- AI 连续 5 轮询问用户是否在听

---

## A06: 结束话术敷衍

**描述**: 对话结束时 AI 未约定下次联系时间或未留下有效信息，敷衍结束。

**检测信号**: AI 结束语仅为"再见""祝您生活愉快"，无后续行动承诺。

**典型案例**:
- 用户表示"我再想想"，AI 直接说"再见"
- 用户说"稍后联系你"，AI 未约定具体时间就结束
```

- [ ] **Step 2: Commit**

```bash
cd ~/.agents/skills && git add auto-qc/ && git commit -m "feat: 添加内置归因规则（6 条归因类别，5 Why 根因分析）"
```

---

### Task 6: templates/worker-prompt.md — Worker 批处理打标模板

**Files:**
- Create: `~/.agents/skills/auto-qc/templates/worker-prompt.md`

这是分发给每个 Worker Agent 的 Prompt 模板，嵌入规则包后使用。

- [ ] **Step 1: 创建 worker-prompt.md**

```markdown
# Worker 质检任务

你是一名质检员。你的任务是：逐条检查对话，判断是否违反给定的质检规则。

## 规则包

以下是你必须逐条检查的规则：

{{RULES_JSON}}

## 批次数据

以下是你需要检查的 {{BATCH_SIZE}} 条对话：

{{CONVERSATIONS}}

## 工作要求

1. **逐条检查**：对每一条对话，逐条过完所有规则。不能跳过任何对话或任何规则。
2. **输出依据**：对于违规的判断，必须附带证据（引用对话原文）。
3. **通过标记**：无违规的对话标记为 `"status": "pass"`，`violations` 为空数组。
4. **抽检详情**：在输出中随机附带 3-5 条对话的详细推理过程（不仅给结论，还要写出"为什么这样判断"）。

## 输出格式

必须输出严格的 JSON，格式如下：

```json
{
  "batch_id": {{BATCH_ID}},
  "rules_checked": ["R01", "R02", "R03", ...],
  "spot_check_details": [
    {
      "id": "对话ID",
      "reasoning": "我检查了这条对话，逐条规则过了一遍。R01: 用户说xxx，AI回应xxx，未触发违规。R02: ..."
    }
  ],
  "results": [
    {
      "id": "对话ID",
      "status": "pass",
      "violations": []
    },
    {
      "id": "对话ID",
      "status": "violation",
      "violations": [
        {
          "rule_id": "R01",
          "rule_name": "规则名称",
          "severity": "高",
          "evidence": "用户: xxx | AI: xxx",
          "suggestion": "改进建议"
        }
      ]
    }
  ]
}
```

**重要**：
- `rules_checked` 必须包含你实际检查过的所有规则 ID
- `spot_check_details` 至少包含 3 条对话的详细推理
- `results` 必须包含批次中的每一条对话，一条不能少
- 只输出 JSON，不要输出其他内容
```

- [ ] **Step 2: Commit**

```bash
cd ~/.agents/skills && git add auto-qc/ && git commit -m "feat: 添加 Worker 质检模板（逐条检查、防偷懒约束、输出格式）"
```

---

### Task 7: templates/attribution-prompt.md — 归因分析 Worker 模板

**Files:**
- Create: `~/.agents/skills/auto-qc/templates/attribution-prompt.md`

- [ ] **Step 1: 创建 attribution-prompt.md**

```markdown
# 归因分析 Worker 任务

你是一名归因分析员。你的任务是：分析对话中什么因素导致没有产出"有意向"的结果。

## 归因规则

以下是归因类别参考（用于识别高频模式）：

{{ATTRIBUTION_RULES}}

## 批次数据

以下是你需要分析的 {{BATCH_SIZE}} 条对话（均为非"有意向"结果）：

{{CONVERSATIONS}}

## 工作要求

1. **逐条分析**：对每一条对话，分析"为什么这段对话没有导致有意向的结果？"
2. **5 Why 追问**：对归因结果做逐步追问（至少 2 层，最多 5 层），找到根因。
3. **归类**：将每条对话归入一个归因类别。如果无法归入已有类别，创建新类别。
4. **统计**：汇总本批次中各类别的数量和占比。

## 输出格式

必须输出严格的 JSON，格式如下：

```json
{
  "batch_id": {{BATCH_ID}},
  "total_conversations": {{BATCH_SIZE}},
  "attribution_results": {
    "A01: AI 未介绍岗位关键信息": {
      "count": 15,
      "ratio": 0.30,
      "whys": [
        {
          "id": "对话ID",
          "intent": "B(不确定)",
          "analysis": {
            "why1": "为什么没谈成？用户未了解岗位基本信息",
            "why2": "为什么未了解？AI 全程未介绍薪资和地点",
            "why3": "为什么未介绍？AI 直接进入要简历环节"
          }
        }
      ]
    }
  },
  "new_categories_discovered": [
    {
      "name": "新类别名称",
      "count": 5,
      "description": "类别描述"
    }
  ]
}
```

**重要**：
- 每条对话必须归入一个类别
- `whys` 中至少包含 3 个典型案例的详细 5 Why 分析
- 只输出 JSON，不要输出其他内容
```

- [ ] **Step 2: Commit**

```bash
cd ~/.agents/skills && git add auto-qc/ && git commit -m "feat: 添加归因分析 Worker 模板（5 Why 归因分析）"
```

---

### Task 8: SKILL.md — Skill 主文件（核心）

**Files:**
- Create: `~/.agents/skills/auto-qc/SKILL.md`

这是 skill 的大脑入口。必须引导 Claude 作为 Supervisor，使用 Agent 工具分发 Worker，调用 Python 做 I/O。

- [ ] **Step 1: 创建 SKILL.md**

```markdown
---
name: auto-qc
description: 外呼通话对话文本质检。用户指定数据 Excel 和规则文件后，自动完成大规模质检（合规检测 + 归因分析），输出 Excel 报告。支持 1w-5w 条数据。
---

# auto-qc 外呼通话文本质检

## 触发方式

用户输入：`/auto-qc --data <excel路径> --rules <规则路径> [--no-attribution] [--keep-temp]`

参数说明：
- `--data`（必需）：源数据 Excel 文件路径
- `--rules`（必需）：合规规则 Markdown 文件路径
- `--no-attribution`（可选）：关闭归因分析，默认开启
- `--keep-temp`（可选）：保留处理过程中的临时文件

## 核心原则

**⚠️ LLM 主导原则**：
- 质检判断（合规检测 + 归因分析）必须由 Claude sub-agent 完成
- Python 只负责 I/O：读 Excel、写 Excel、预处理对话、拆分批次
- 禁止用 Python 代码做质检判断

**Harness 范式**：统一框架，两次运行
- 通道一：注入合规规则 → 合规检测
- 通道二：注入归因规则 → 归因分析
- 两通道结构相同：拆分 → 分发 → 收集 → 校验 → 汇总

## 执行流程

### Step 0: 环境检查

1. 检查 Python 依赖是否已安装：
   ```bash
   python -c "import openpyxl, pandas, json_repair; print('OK')"
   ```
   如果报错，执行 `uv pip install -r ~/.agents/skills/auto-qc/requirements.txt`

2. 验证文件路径：
   - 检查 `--data` Excel 文件是否存在
   - 检查 `--rules` 规则文件是否存在
   - 检查 skill 目录下的模板文件是否存在

### Step 1: 解析规则

运行规则解析器，将 rules.md 转为 JSON 规则包：

```bash
cd ~/.agents/skills/auto-qc/scripts
python rules_parser.py --rules <规则路径> --output ~/.agents/skills/auto-qc/tmp/rules_package.json
```

读取 `rules_package.json`，确认规则数量和规则 ID 列表。

### Step 2: 加载数据 + 拆分批次

运行数据加载器：

```bash
cd ~/.agents/skills/auto-qc/scripts
mkdir -p ~/.agents/skills/auto-qc/tmp
python data_loader.py load --data <数据路径> --batch-size 100 --output ~/.agents/skills/auto-qc/tmp/batches
```

这会输出：
- N 个批次 JSON 文件：`batch_1.json`, `batch_2.json`, ...
- 每个文件包含 100 条对话（预处理为可读格式）

确认批次数量，创建进度文件 `~/.agents/skills/auto-qc/tmp/progress.json`：

```json
{
  "total_batches": <N>,
  "completed_batches": 0,
  "batch_status": {},
  "completed_ids": [],
  "failed_batches": [],
  "status": "running",
  "started_at": "<当前时间>",
  "updated_at": "<当前时间>"
}
```

### Step 3: 分发 Worker（合规检测）

**每次并发 5 个 Worker Agent**，循环处理所有批次。

对每个批次：
1. 读取 `batch_N.json`
2. 读取 `worker-prompt.md` 模板
3. 将规则包 JSON + 批次数据 + 模板组合成 Worker Prompt
4. 使用 `Agent` 工具启动 Worker sub-agent，传入组合后的 Prompt
5. 收集 Worker 返回的 JSON 结果
6. 用 `json_repair` 修复可能的 JSON 格式问题
7. 校验结果：
   - 结果数量 == 批次数量（100 条）？
   - 每条都有 `id`？
   - `rules_checked` 包含所有规则 ID？
8. 校验通过 → 保存到 `~/.agents/skills/auto-qc/tmp/batch_result_N.json`
9. 更新进度文件

**失败重试**：
- 校验失败 → 重试该批次，最多 3 次
- 3 次都失败 → 记录到 `~/.agents/skills/auto-qc/tmp/failed_batches.json`，继续下一批

**进度汇报**：
- 每完成 10 批（或 10%），向用户汇报一次："已完成 X/N 批（XX%）"

### Step 4: 交叉验证

合规检测全部完成后，执行交叉验证：

1. 统计整体违规率
2. 按违规/无违规分层抽样：
   - 违规组抽 2%，无违规组抽 1%
3. 将抽中的对话重新组合成批次，启动新的 Worker sub-agent 做 double-check
4. 对比两次结果（同一条对话同一个规则，两次判断是否一致）
5. 计算差异率：
   - < 5%：正常
   - 5%-10%：标记可疑
   - > 10%：扩大抽样到 5%

### Step 5: 归因分析（可选）

如果用户未指定 `--no-attribution`，执行归因分析：

1. 从 Excel 中过滤出意向结果 ≠ "A(有意向)" 的对话
2. 运行数据加载器（带过滤）：
   ```bash
   python data_loader.py load --data <数据路径> --batch-size 100 --output ~/.agents/skills/auto-qc/tmp/attribution_batches --exclude-intent "A(有意向)"
   ```
3. 读取 `attribution-rules.md` 内置归因规则
4. 读取 `attribution-prompt.md` 模板
5. 同样并发 5 个 Worker Agent 逐批归因
6. 收集结果 → 校验 → 保存到 `~/.agents/skills/auto-qc/tmp/attribution_results.json`

### Step 6: 生成报告

1. 合并所有批次结果为完整质检报告 JSON：
   ```bash
   python -c "
   import json, glob
   results = []
   for f in sorted(glob.glob('~/.agents/skills/auto-qc/tmp/batch_result_*.json')):
       with open(f) as fh:
           results.extend(json.load(fh)['results'])
   with open('~/.agents/skills/auto-qc/tmp/all_qc_results.json', 'w') as fh:
       json.dump(results, fh, ensure_ascii=False, indent=2)
   "
   ```

2. 生成统计概览 JSON：
   - 总对话数
   - 违规率（违规对话数 / 总对话数）
   - 各规则违规次数
   - 严重程度分布

3. 生成报告 Excel：
   ```bash
   python report_writer.py write \
     --output <报告输出路径> \
     --qc-results ~/.agents/skills/auto-qc/tmp/all_qc_results.json \
     --attribution ~/.agents/skills/auto-qc/tmp/attribution_results.json \
     --stats ~/.agents/skills/auto-qc/tmp/stats.json
   ```

4. 报告路径告知用户

### Step 7: 清理

- 如果用户未指定 `--keep-temp`，清理临时文件：
  ```bash
  python report_writer.py cleanup --dir ~/.agents/skills/auto-qc/tmp
  ```
- 否则提示用户临时文件保留位置

## 断点续跑

每次启动时检查 `~/.agents/skills/auto-qc/tmp/progress.json`：
- 如果存在且状态不是 "done" → 提示用户："检测到上次中断的进度（已完成 X/Y 批），是否继续？"
- 用户确认 → 使用 `data_loader.py resume` 从未完成批次继续
- 用户否认 → 清空进度，从头开始

## 错误处理

| 错误 | 处理方式 |
|------|----------|
| Excel 文件不存在 | 提示用户检查路径 |
| 规则文件不存在 | 提示用户检查路径 |
| 列名匹配失败 | 提示用户，列出当前表头 |
| Worker JSON 解析失败 | 用 json_repair 尝试修复，修复失败则重试批次 |
| Worker 超时/崩溃 | 重试该批次，最多 3 次 |
| 3 次重试都失败 | 记录到 failed_batches.json，不阻塞流程 |
| 交叉验证差异率 > 10% | 扩大抽样比例到 5%，重新验证 |

## 文件结构

```
~/.agents/skills/auto-qc/
├── SKILL.md                        # 本文件
├── templates/
│   ├── worker-prompt.md            # Worker 打标模板
│   ├── attribution-prompt.md       # 归因分析模板
│   └── attribution-rules.md        # 内置归因规则
├── scripts/
│   ├── data_loader.py              # 数据加载 + 预处理 + 批次拆分
│   ├── report_writer.py            # 报告生成 + 临时文件清理
│   └── rules_parser.py             # Markdown 规则解析
└── requirements.txt                # Python 依赖
```
```

- [ ] **Step 2: Commit**

```bash
cd ~/.agents/skills && git add auto-qc/ && git commit -m "feat: 添加 SKILL.md（主入口、Sub-agent 调度、完整流程）"
```

---

### Task 9: 端到端测试 — 500 条数据验证

**Files:**
- Test data: `C:\Users\dongyi\Desktop\pi-500-data.xlsx`
- Test rules: `C:\Users\dongyi\myprojects\auto-pi\auto-pi\harness\output\2026-06-01_101119\phase5\rules.md`

- [ ] **Step 1: 安装依赖**

```bash
cd ~/.agents/skills/auto-qc && uv pip install -r requirements.txt
```

- [ ] **Step 2: 测试规则解析**

```bash
cd ~/.agents/skills/auto-qc/scripts
python rules_parser.py --rules "C:\Users\dongyi\myprojects\auto-pi\auto-pi\harness\output\2026-06-01_101119\phase5\rules.md" --output ~/.agents/skills/auto-qc/tmp/test_rules.json
cat ~/.agents/skills/auto-qc/tmp/test_rules.json | python -c "import json,sys; d=json.load(sys.stdin); print(f'解析到 {len(d[\"rules\"])} 条规则')"
```

Expected: 解析出 13 条规则。

- [ ] **Step 3: 测试数据加载 + 预处理**

```bash
cd ~/.agents/skills/auto-qc/scripts
mkdir -p ~/.agents/skills/auto-qc/tmp/test_batches
python data_loader.py load --data "C:\Users\dongyi\Desktop\pi-500-data.xlsx" --batch-size 100 --output ~/.agents/skills/auto-qc/tmp/test_batches
```

Expected: 输出 `{"total": 500, "num_batches": 5, "output_dir": "..."}`，生成 5 个 batch_*.json 文件。

- [ ] **Step 4: 验证批次内容**

```bash
python -c "
import json
with open('$HOME/.agents/skills/auto-qc/tmp/test_batches/batch_1.json') as f:
    batch = json.load(f)
print(f'Batch 1: {batch[\"total\"]} 条对话')
print(f'第一条对话ID: {batch[\"conversations\"][0][\"id\"]}')
print(f'第一条对话预览: {batch[\"conversations\"][0][\"conversation\"][:200]}')
"
```

Expected: 对话已转为 "AI: ... / 用户: ..." 格式。

- [ ] **Step 5: 手动触发一次 Worker 验证**

用第一条批次数据测试 Worker Prompt 是否正常工作：

1. 读取 `batch_1.json`
2. 读取规则包 JSON
3. 组合成 Worker Prompt
4. 手动启动一个 Agent 测试

如果 Worker 返回正确的 JSON 格式，说明整条链路通畅。

- [ ] **Step 6: 提交测试 commit（不提交代码变更，仅确认测试通过）**

所有测试通过后，确认 skill 结构完整：

```bash
ls -R ~/.agents/skills/auto-qc/
```

Expected: 看到 SKILL.md、templates/、scripts/、requirements.txt 全部存在。

---

## Spec 覆盖检查

| Spec 章节 | 对应 Task | 状态 |
|-----------|-----------|------|
| 2. 系统架构 | Task 1-8 | ✅ SKILL.md 定义统一框架 |
| 3. 规则解析 | Task 4 | ✅ rules_parser.py |
| 4. 批次与并发 | Task 6, 8 | ✅ worker-prompt.md + SKILL.md Step 3 |
| 4.2 Worker 防偷懒 | Task 6 | ✅ rules_checked + spot_check_details |
| 5. 校验机制 | Task 8 | ✅ SKILL.md Step 3-4 |
| 5.3 交叉验证 | Task 8 | ✅ SKILL.md Step 4 |
| 6. 归因分析 | Task 5, 7, 8 | ✅ attribution-rules + prompt + SKILL.md Step 5 |
| 7. 输出报告 | Task 3 | ✅ report_writer.py 三个 sheet |
| 8. Skill 结构 | Task 1-8 | ✅ 完整文件结构 |
| 10. 进度反馈 | Task 2, 8 | ✅ data_loader.py + SKILL.md |
| 11. 失败重试 | Task 8 | ✅ SKILL.md 错误处理 |
| 13. 临时文件清理 | Task 3 | ✅ report_writer.py cleanup |
| 15. 核心约束 | Task 8 | ✅ SKILL.md LLM 主导原则 |
| 16. 对话预处理 | Task 2 | ✅ data_loader.py preprocess_conversation |
| 17. Excel 列识别 | Task 2 | ✅ data_loader.py match_columns |
| 18. 容错机制 | Task 8 | ✅ json_repair + 重试 |
| 19. Skill 可移植性 | Task 1, 8 | ✅ requirements.txt + 自包含结构 |
| 20. Python 依赖 | Task 1 | ✅ requirements.txt |

## 占位符扫描

无 TBD、TODO、"fill in later"。

## 类型一致性

- 所有 JSON 结构使用一致的字段名：`id`, `status`, `violations`, `rule_id`, `severity`, `evidence`, `suggestion`
- Python 脚本通过 CLI 子命令调用，输入输出均为 JSON
- SKILL.md 中引用的文件路径全部为绝对路径 `~/.agents/skills/auto-qc/`
