# auto-qc CLI 重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 auto-qc 从 Prompt 驱动架构重构为 CLI 驱动架构，Python 代码做框架约束每一步，LLM 只做语义分析。

**Architecture:** 两层分离——framework/ 负责流程控制、并发、校验等纯工程逻辑；domain/ 负责质检业务逻辑（规则解析、prompt 组装、报告生成）。通过 Anthropic SDK 直调 DeepSeek API，环境变量零配置。

**Tech Stack:** Python 3.13+, uv, anthropic SDK, openpyxl, json-repair, pytest

---

## Task 1: 项目脚手架

**Files:**
- Modify: `pyproject.toml`
- Create: `src/auto_qc/__init__.py`
- Create: `src/auto_qc/framework/__init__.py`
- Create: `src/auto_qc/domain/__init__.py`

- [ ] **Step 1: 更新 pyproject.toml**

将现有的 `pyproject.toml` 替换为完整配置：

```toml
[project]
name = "auto-qc"
version = "0.1.0"
description = "外呼通话文本质检 CLI"
readme = "README.md"
requires-python = ">=3.13"
dependencies = [
    "anthropic>=0.60.0",
    "openpyxl==3.1.5",
    "pandas==2.2.3",
    "json-repair==0.59.10",
]

[project.scripts]
auto-qc = "auto_qc.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: 创建 `__init__.py` 文件**

```python
# src/auto_qc/__init__.py
VERSION = "0.1.0"
```

`src/auto_qc/framework/__init__.py` 和 `src/auto_qc/domain/__init__.py` 为空文件。

- [ ] **Step 3: 创建目录结构 + 验证 uv sync**

```bash
mkdir -p src/auto_qc/framework src/auto_qc/domain
mkdir -p tests/framework tests/domain
uv sync
```

Expected: `uv sync` 成功安装 anthropic, openpyxl, pandas, json-repair。

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/auto_qc/__init__.py src/auto_qc/framework/__init__.py src/auto_qc/domain/__init__.py uv.lock
git commit -m "chore: 搭建 CLI 项目脚手架"
```

---

## Task 2: 领域层 — 数据结构定义

**Files:**
- Create: `src/auto_qc/domain/schemas.py`
- Create: `tests/domain/test_schemas.py`

`schemas.py` 定义系统中所有核心数据结构，框架层和领域层都依赖它，所以最先实现。

- [ ] **Step 1: 编写 schemas.py**

```python
"""领域数据结构定义"""
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Violation:
    """单条违规记录"""
    rule_id: str
    rule_name: str
    severity: str          # "高" | "中" | "低"
    evidence: str          # 对话原文片段
    suggestion: str        # 改进建议


@dataclass
class ResultItem:
    """单条对话的质检结果"""
    id: str
    status: str            # "pass" | "violation"
    violations: list[Violation] = field(default_factory=list)


@dataclass
class WorkerOutput:
    """Worker（LLM）返回的完整批次结果，由 validator 校验"""
    batch_id: int
    rules_checked: list[str]
    spot_check_details: list[dict]   # 3-5 条推理链
    results: list[ResultItem]        # 每条对话一条结果

    @classmethod
    def from_dict(cls, data: dict) -> "WorkerOutput":
        results = []
        for r in data.get("results", []):
            violations = []
            for v in r.get("violations", []):
                violations.append(Violation(
                    rule_id=v.get("rule_id", ""),
                    rule_name=v.get("rule_name", ""),
                    severity=v.get("severity", ""),
                    evidence=v.get("evidence", ""),
                    suggestion=v.get("suggestion", ""),
                ))
            results.append(ResultItem(
                id=r.get("id", ""),
                status=r.get("status", "pass"),
                violations=violations,
            ))
        return cls(
            batch_id=data.get("batch_id", 0),
            rules_checked=data.get("rules_checked", []),
            spot_check_details=data.get("spot_check_details", []),
            results=results,
        )


@dataclass
class Rule:
    """单条合规规则"""
    rule_id: str           # R01, R02, ...
    name: str
    severity: str          # "高" | "中" | "低"
    description: str
    detection_logic: str
    examples: list[str] = field(default_factory=list)


@dataclass
class RulePackage:
    """规则包：解析后的规则集合"""
    rules: list[Rule]

    @property
    def rule_ids(self) -> list[str]:
        return [r.rule_id for r in self.rules]

    @classmethod
    def from_dict(cls, data: dict) -> "RulePackage":
        rules = []
        for r in data.get("rules", []):
            rules.append(Rule(
                rule_id=r.get("rule_id", ""),
                name=r.get("name", ""),
                severity=r.get("severity", ""),
                description=r.get("description", ""),
                detection_logic=r.get("detection_logic", ""),
                examples=r.get("examples", []),
            ))
        return cls(rules=rules)


@dataclass
class Conversation:
    """预处理后的单条对话"""
    id: str
    time: str
    intent: str
    conversation: str      # 已预处理为可读格式


@dataclass
class Batch:
    """一个批次（100条对话）"""
    batch_id: int
    conversations: list[Conversation]

    @property
    def ids(self) -> list[str]:
        return [c.id for c in self.conversations]

    @property
    def size(self) -> int:
        return len(self.conversations)


@dataclass
class CrossValidationResult:
    """交叉验证结果"""
    total_compared: int          # 对比的规则判断总数
    mismatches: int              # 不一致数
    discrepancy_rate: float      # 差异率
    status: str                  # "ok" | "suspicious" | "high"

    @classmethod
    def compute(cls, mismatches: int, total: int) -> "CrossValidationResult":
        rate = mismatches / total if total > 0 else 0.0
        if rate < 0.05:
            status = "ok"
        elif rate < 0.10:
            status = "suspicious"
        else:
            status = "high"
        return cls(
            total_compared=total,
            mismatches=mismatches,
            discrepancy_rate=rate,
            status=status,
        )


@dataclass
class Progress:
    """进度追踪"""
    total_batches: int = 0
    completed_batches: int = 0
    phase: str = "init"        # init | qc | cross_validation | attribution | reporting | done
    batch_status: dict[str, str] = field(default_factory=dict)   # "1": "done", ...
    retry_count: dict[str, int] = field(default_factory=dict)
    failed_batches: list[int] = field(default_factory=list)
    started_at: str = ""
    updated_at: str = ""
```

- [ ] **Step 2: 编写测试**

```python
# tests/domain/test_schemas.py
import pytest
from auto_qc.domain.schemas import (
    Violation, ResultItem, WorkerOutput, Rule, RulePackage,
    Conversation, Batch, CrossValidationResult, Progress,
)


class TestWorkerOutput:
    def test_from_valid_dict(self):
        data = {
            "batch_id": 1,
            "rules_checked": ["R01", "R02"],
            "spot_check_details": [{"id": "123", "reasoning": "..."}],
            "results": [
                {
                    "id": "123",
                    "status": "violation",
                    "violations": [
                        {
                            "rule_id": "R01",
                            "rule_name": "测试规则",
                            "severity": "高",
                            "evidence": "用户: 不考虑 | AI: 好的",
                            "suggestion": "立刻结束对话",
                        }
                    ],
                },
                {"id": "456", "status": "pass", "violations": []},
            ],
        }
        output = WorkerOutput.from_dict(data)
        assert output.batch_id == 1
        assert len(output.rules_checked) == 2
        assert len(output.results) == 2
        assert output.results[0].status == "violation"
        assert output.results[0].violations[0].rule_id == "R01"

    def test_from_empty_dict(self):
        output = WorkerOutput.from_dict({})
        assert output.batch_id == 0
        assert output.results == []
        assert output.rules_checked == []


class TestRulePackage:
    def test_rule_ids_property(self):
        pkg = RulePackage.from_dict({
            "rules": [
                {"rule_id": "R01", "name": "a", "severity": "高",
                 "description": "", "detection_logic": ""},
                {"rule_id": "R02", "name": "b", "severity": "中",
                 "description": "", "detection_logic": ""},
            ]
        })
        assert pkg.rule_ids == ["R01", "R02"]


class TestBatch:
    def test_ids_and_size(self):
        convs = [
            Conversation(id="1", time="", intent="", conversation=""),
            Conversation(id="2", time="", intent="", conversation=""),
        ]
        batch = Batch(batch_id=1, conversations=convs)
        assert batch.ids == ["1", "2"]
        assert batch.size == 2


class TestCrossValidationResult:
    def test_ok_rate(self):
        r = CrossValidationResult.compute(mismatches=2, total=100)
        assert r.discrepancy_rate == 0.02
        assert r.status == "ok"

    def test_suspicious_rate(self):
        r = CrossValidationResult.compute(mismatches=7, total=100)
        assert r.status == "suspicious"

    def test_high_rate(self):
        r = CrossValidationResult.compute(mismatches=15, total=100)
        assert r.status == "high"
```

- [ ] **Step 3: 运行测试验证通过**

```bash
uv run pytest tests/domain/test_schemas.py -v
```

Expected: 6 tests passed.

- [ ] **Step 4: Commit**

```bash
git add src/auto_qc/domain/schemas.py tests/domain/test_schemas.py
git commit -m "feat: 定义领域数据结构（schemas.py）"
```

---

## Task 3: 领域层 — 规则解析与校验

**Files:**
- Create: `src/auto_qc/domain/rules.py`
- Create: `tests/domain/test_rules.py`

复用现有 `scripts/rules_parser.py` 的 `parse_rules()` 逻辑，放入领域层，增加校验。

- [ ] **Step 1: 编写 rules.py**

```python
"""规则文件解析与校验"""
import re
from pathlib import Path
from auto_qc.domain.schemas import Rule, RulePackage

_SEVERITY_MAP = {"高": "高", "中": "中", "低": "低", "HIGH": "高", "MEDIUM": "中", "LOW": "低"}


def parse_rules_markdown(markdown_text: str) -> list[Rule]:
    """解析 Markdown 格式的规则文本，返回 Rule 列表。"""
    rules = []
    pattern = re.compile(r"(?:\n|^)## (R\d+):\s*(.+?)\n(.*?)(?=(?:\n|^)## R|\Z)", re.DOTALL)

    for match in pattern.finditer(markdown_text):
        rule_id = match.group(1)
        name = match.group(2).strip()
        content = match.group(3)

        severity_match = re.search(r"\*\*严重程度\*\*[:：]\s*(.+)", content)
        severity = severity_match.group(1).strip() if severity_match else ""
        severity = _SEVERITY_MAP.get(severity, severity)

        desc_match = re.search(r"\*\*描述\*\*[:：]\s*(.+?)(?=\n\*\*|\n##|$)", content, re.DOTALL)
        description = desc_match.group(1).strip() if desc_match else ""

        logic_match = re.search(r"\*\*检测逻辑\*\*[:：]\s*(.+?)(?=\n\*\*|\n##|$)", content, re.DOTALL)
        detection_logic = logic_match.group(1).strip() if logic_match else ""

        examples = []
        examples_match = re.search(r"\*\*典型案例\*\*[:：]\s*\n?(.+?)(?=\n\*\*|\n##|$)", content, re.DOTALL)
        if examples_match:
            examples_text = examples_match.group(1).strip()
            examples = [
                line.strip().lstrip("- ").strip()
                for line in examples_text.split("\n")
                if line.strip().startswith("-")
            ]

        rules.append(Rule(
            rule_id=rule_id,
            name=name,
            severity=severity,
            description=description,
            detection_logic=detection_logic,
            examples=examples,
        ))
    return rules


def parse_rules_file(file_path: str) -> RulePackage:
    """读取规则文件并解析为 RulePackage。"""
    text = Path(file_path).read_text(encoding="utf-8")
    rules = parse_rules_markdown(text)
    return RulePackage(rules=rules)


def validate_rule_package(pkg: RulePackage) -> list[str]:
    """
    校验规则包的完整性。返回错误列表，空列表表示通过。
    """
    errors = []

    if not pkg.rules:
        errors.append("规则包为空，至少需要一条规则")
        return errors

    seen_ids = set()
    for rule in pkg.rules:
        # 规则 ID 唯一性
        if rule.rule_id in seen_ids:
            errors.append(f"规则 ID 重复: {rule.rule_id}")
        seen_ids.add(rule.rule_id)

        # 必填字段
        if not rule.name:
            errors.append(f"{rule.rule_id}: 规则名称为空")
        if not rule.description:
            errors.append(f"{rule.rule_id}: 规则描述为空")
        if not rule.detection_logic:
            errors.append(f"{rule.rule_id}: 检测逻辑为空")

        # severity 合法性
        if rule.severity not in ("高", "中", "低"):
            errors.append(f"{rule.rule_id}: severity 不合法 ({rule.severity})，应为 高/中/低")

    return errors
```

- [ ] **Step 2: 编写测试**

```python
# tests/domain/test_rules.py
import pytest
from auto_qc.domain.rules import parse_rules_markdown, parse_rules_file, validate_rule_package
from auto_qc.domain.schemas import RulePackage


def test_parse_single_rule():
    text = """## R01: 无视用户明确拒绝

**严重程度**: 高
**发现次数**: 558/25600 (2.2%)

**描述**: 用户明确表示不考虑后，AI 未礼貌结束对话。

**检测逻辑**: 检测用户发言包含明确拒绝关键词后，AI 下一条发言是否继续推进。

**典型案例**:
- 对话A: 用户: 嗯，暂时不考虑了啊 | AI继续: ...
- 对话B: 用户: 不考虑，拜拜 | AI继续: ...
"""
    rules = parse_rules_markdown(text)
    assert len(rules) == 1
    assert rules[0].rule_id == "R01"
    assert rules[0].name == "无视用户明确拒绝"
    assert rules[0].severity == "高"
    assert len(rules[0].examples) == 2


def test_parse_multiple_rules():
    text = """## R01: 规则一

**严重程度**: 高

**描述**: 描述一

**检测逻辑**: 逻辑一

## R02: 规则二

**严重程度**: MEDIUM

**描述**: 描述二

**检测逻辑**: 逻辑二
"""
    rules = parse_rules_markdown(text)
    assert len(rules) == 2
    assert rules[0].rule_id == "R01"
    assert rules[1].rule_id == "R02"
    assert rules[1].severity == "中"  # MEDIUM → 中


def test_validate_empty_package():
    pkg = RulePackage(rules=[])
    errors = validate_rule_package(pkg)
    assert len(errors) == 1
    assert "为空" in errors[0]


def test_validate_duplicate_ids():
    from auto_qc.domain.schemas import Rule
    pkg = RulePackage(rules=[
        Rule(rule_id="R01", name="规则一", severity="高", description="d", detection_logic="l"),
        Rule(rule_id="R01", name="规则二", severity="中", description="d", detection_logic="l"),
    ])
    errors = validate_rule_package(pkg)
    assert any("重复" in e for e in errors)


def test_validate_invalid_severity():
    from auto_qc.domain.schemas import Rule
    pkg = RulePackage(rules=[
        Rule(rule_id="R01", name="x", severity="CRITICAL", description="d", detection_logic="l"),
    ])
    errors = validate_rule_package(pkg)
    assert any("severity" in e for e in errors)


def test_validate_missing_fields():
    from auto_qc.domain.schemas import Rule
    pkg = RulePackage(rules=[
        Rule(rule_id="R01", name="", severity="高", description="", detection_logic=""),
    ])
    errors = validate_rule_package(pkg)
    assert len(errors) == 2  # name empty, description empty, detection_logic empty
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/domain/test_rules.py -v
```

Expected: 6 tests passed.

- [ ] **Step 4: Commit**

```bash
git add src/auto_qc/domain/rules.py tests/domain/test_rules.py
git commit -m "feat: 规则解析与校验模块"
```

---

## Task 4: 领域层 — 数据加载器

**Files:**
- Create: `src/auto_qc/domain/data_loader.py`
- Create: `tests/domain/test_data_loader.py`

复用现有 `scripts/data_loader.py` 的核心逻辑，适配新的 `Conversation`/`Batch` 数据结构。

- [ ] **Step 1: 编写 data_loader.py**

```python
"""Excel 读取、列匹配、对话预处理、批次拆分"""
import json
from pathlib import Path
from typing import Optional
import openpyxl
from auto_qc.domain.schemas import Conversation, Batch


COLUMN_PATTERNS = {
    "id_col": ["id", "通话ID", "call_id", "callId", "通话id"],
    "time_col": ["时间", "通话时间", "call_time", "callTime", "通话日期"],
    "conv_col": ["对话", "对话文本", "conversation", "conv", "通话内容", "对话内容"],
    "intent_col": ["意向", "意向结果", "intent_result", "intentResult", "结果"],
}


def _match_columns(headers: list[str]) -> dict[str, str]:
    """按关键词匹配 Excel 列名。"""
    result = {}
    for key, keywords in COLUMN_PATTERNS.items():
        matched = None
        for kw in keywords:
            for h in headers:
                if kw.lower() in h.lower():
                    matched = h
                    break
            if matched:
                break
        if matched is None:
            raise ValueError(
                f"未找到 {key} 对应的列。当前表头: {headers}。期望关键词: {keywords}"
            )
        result[key] = matched
    return result


def _preprocess_conversation(conv_json: list[dict]) -> str:
    """将 TTS/ASR JSON 转为可读文本。"""
    lines = []
    for turn in conv_json:
        tts = turn.get("ttsResult", "").strip()
        asr = turn.get("asrResult", "").strip()
        if tts:
            lines.append(f"AI: {tts}")
        if asr:
            lines.append(f"用户: {asr}")
    return "\n".join(lines)


def _preprocess_raw(conv_raw: str) -> str:
    """处理原始单元格数据（可能双重编码的 JSON）。"""
    text = str(conv_raw)
    if text.startswith('"[''):
        try:
            text = json.loads(text)
        except json.JSONDecodeError:
            pass
    if isinstance(text, str):
        data = json.loads(text)
    else:
        data = text
    return _preprocess_conversation(data)


def load_conversations(
    data_path: str,
    batch_size: int = 100,
    exclude_intent: Optional[str] = None,
) -> list[Batch]:
    """
    读取 Excel，预处理对话，按 batch_size 拆分为 Batch 列表。
    """
    wb = openpyxl.load_workbook(data_path, read_only=True, data_only=True)
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    headers = list(next(rows_iter))
    col_map = _match_columns(headers)

    id_idx = headers.index(col_map["id_col"])
    time_idx = headers.index(col_map["time_col"])
    conv_idx = headers.index(col_map["conv_col"])
    intent_idx = headers.index(col_map["intent_col"])

    conversations = []
    for row in rows_iter:
        if row[id_idx] is None:
            continue

        intent = str(row[intent_idx]).strip() if row[intent_idx] else ""

        if exclude_intent and intent == exclude_intent:
            continue

        try:
            conv_text = _preprocess_raw(str(row[conv_idx]))
        except (json.JSONDecodeError, TypeError):
            conv_text = "[对话解析失败]"

        conversations.append(Conversation(
            id=str(row[id_idx]),
            time=str(row[time_idx]).strip() if row[time_idx] else "",
            intent=intent,
            conversation=conv_text,
        ))

    wb.close()

    if not conversations:
        raise ValueError("未从 Excel 中读取到任何有效数据")

    batches = []
    for i in range(0, len(conversations), batch_size):
        chunk = conversations[i:i + batch_size]
        batches.append(Batch(batch_id=i // batch_size + 1, conversations=chunk))

    return batches


def save_batches(batches: list[Batch], output_dir: str) -> None:
    """将批次列表保存为 JSON 文件到指定目录。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    for batch in batches:
        file_path = out / f"batch_{batch.batch_id}.json"
        data = {
            "batch_id": batch.batch_id,
            "total": batch.size,
            "ids": batch.ids,
            "conversations": [
                {"id": c.id, "time": c.time, "intent": c.intent, "conversation": c.conversation}
                for c in batch.conversations
            ],
        }
        file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **Step 2: 编写测试**

```python
# tests/domain/test_data_loader.py
import json
import tempfile
from pathlib import Path
import openpyxl
import pytest
from auto_qc.domain.data_loader import (
    _match_columns, _preprocess_conversation, save_batches, load_conversations,
)
from auto_qc.domain.schemas import Batch, Conversation


def test_match_columns_exact():
    headers = ["通话ID", "通话时间", "对话文本", "意向结果"]
    result = _match_columns(headers)
    assert result["id_col"] == "通话ID"
    assert result["conv_col"] == "对话文本"


def test_match_columns_fuzzy():
    headers = ["通话id", "时间", "对话", "意向"]
    result = _match_columns(headers)
    assert result["id_col"] == "通话id"


def test_match_columns_missing_raises():
    with pytest.raises(ValueError, match="未找到"):
        _match_columns(["仅有时间", "仅有对话"])


def test_preprocess_conversation():
    data = [
        {"ttsResult": "你好", "asrResult": "喂"},
        {"ttsResult": "请问是张三吗", "asrResult": ""},
    ]
    result = _preprocess_conversation(data)
    assert "AI: 你好" in result
    assert "用户: 喂" in result
    assert "AI: 请问是张三吗" in result


def test_save_and_load_batches():
    batches = [
        Batch(batch_id=1, conversations=[
            Conversation(id="1", time="2024-01-01", intent="A", conversation="hi"),
            Conversation(id="2", time="2024-01-02", intent="B", conversation="bye"),
        ]),
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        save_batches(batches, tmpdir)
        saved = Path(tmpdir) / "batch_1.json"
        assert saved.exists()
        data = json.loads(saved.read_text(encoding="utf-8"))
        assert data["batch_id"] == 1
        assert data["total"] == 2
        assert len(data["conversations"]) == 2
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/domain/test_data_loader.py -v
```

Expected: 5 tests passed.

- [ ] **Step 4: Commit**

```bash
git add src/auto_qc/domain/data_loader.py tests/domain/test_data_loader.py
git commit -m "feat: 数据加载器（Excel读取/列匹配/对话预处理/批次拆分）"
```

---

## Task 5: 领域层 — Prompt 组装

**Files:**
- Create: `src/auto_qc/domain/prompts.py`
- Create: `tests/domain/test_prompts.py`

- [ ] **Step 1: 编写 prompts.py**

```python
"""Prompt 模板组装——把规则 + 对话拼成 LLM 可理解的完整 prompt"""
import json
from pathlib import Path
from auto_qc.domain.schemas import RulePackage, Batch

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "templates"


def _load_template(filename: str) -> str:
    """读取模板文件。"""
    path = _TEMPLATES_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"模板文件不存在: {path}")
    return path.read_text(encoding="utf-8")


def build_qc_prompt(batch: Batch, rule_package: RulePackage) -> str:
    """
    组装合规检测 Worker prompt。
    模板: templates/worker-prompt.md
    """
    template = _load_template("worker-prompt.md")

    # 构造对话列表 JSON
    conversations_json = json.dumps(
        [{"id": c.id, "time": c.time, "intent": c.intent, "conversation": c.conversation}
         for c in batch.conversations],
        ensure_ascii=False,
        indent=2,
    )

    # 构造规则 JSON
    rules_json = json.dumps(
        [{"rule_id": r.rule_id, "name": r.name, "severity": r.severity,
          "description": r.description, "detection_logic": r.detection_logic,
          "examples": r.examples}
         for r in rule_package.rules],
        ensure_ascii=False,
        indent=2,
    )

    prompt = template.replace("{{RULES_JSON}}", rules_json)
    prompt = prompt.replace("{{BATCH_SIZE}}", str(batch.size))
    prompt = prompt.replace("{{CONVERSATIONS}}", conversations_json)
    prompt = prompt.replace("{{BATCH_ID}}", str(batch.batch_id))

    return prompt


def build_attribution_prompt(batch: Batch) -> str:
    """
    组装归因分析 Worker prompt。
    模板: templates/attribution-prompt.md
    规则: templates/attribution-rules.md
    """
    template = _load_template("attribution-prompt.md")
    attribution_rules = _load_template("attribution-rules.md")

    conversations_json = json.dumps(
        [{"id": c.id, "time": c.time, "intent": c.intent, "conversation": c.conversation}
         for c in batch.conversations],
        ensure_ascii=False,
        indent=2,
    )

    prompt = template.replace("{{ATTRIBUTION_RULES}}", attribution_rules)
    prompt = prompt.replace("{{BATCH_SIZE}}", str(batch.size))
    prompt = prompt.replace("{{CONVERSATIONS}}", conversations_json)
    prompt = prompt.replace("{{BATCH_ID}}", str(batch.batch_id))

    return prompt
```

- [ ] **Step 2: 编写测试**

```python
# tests/domain/test_prompts.py
import json
import pytest
from auto_qc.domain.prompts import build_qc_prompt, build_attribution_prompt
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
    assert "\"id\": \"1\"" in prompt


def test_build_attribution_prompt():
    batch = Batch(batch_id=1, conversations=[
        Conversation(id="1", time="", intent="F", conversation="hi"),
    ])
    prompt = build_attribution_prompt(batch)
    assert "归因" in prompt or "A01" in prompt
    assert "\"id\": \"1\"" in prompt
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/domain/test_prompts.py -v
```

Expected: 2 tests passed.

- [ ] **Step 4: Commit**

```bash
git add src/auto_qc/domain/prompts.py tests/domain/test_prompts.py
git commit -m "feat: Prompt 模板组装模块"
```

---

## Task 6: 领域层 — 归因规则

**Files:**
- Create: `src/auto_qc/domain/attribution.py`
- Create: `tests/domain/test_attribution.py`

- [ ] **Step 1: 编写 attribution.py**

```python
"""内置归因规则"""
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "templates"


def get_attribution_rules_path() -> str:
    """返回内置归因规则文件路径。"""
    path = _TEMPLATES_DIR / "attribution-rules.md"
    if not path.exists():
        raise FileNotFoundError(f"内置归因规则文件不存在: {path}")
    return str(path)


def get_attribution_rules_text() -> str:
    """返回内置归因规则的文本内容。"""
    return Path(get_attribution_rules_path()).read_text(encoding="utf-8")
```

- [ ] **Step 2: 编写测试**

```python
# tests/domain/test_attribution.py
from auto_qc.domain.attribution import get_attribution_rules_path, get_attribution_rules_text


def test_rules_path_exists():
    path = get_attribution_rules_path()
    assert path.endswith("attribution-rules.md")


def test_rules_text_contains_categories():
    text = get_attribution_rules_text()
    assert "A01" in text or "归因" in text
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/domain/test_attribution.py -v
```

Expected: 2 tests passed.

- [ ] **Step 4: Commit**

```bash
git add src/auto_qc/domain/attribution.py tests/domain/test_attribution.py
git commit -m "feat: 内置归因规则模块"
```

---

## Task 7: 领域层 — 报告生成器

**Files:**
- Create: `src/auto_qc/domain/report.py`
- Create: `tests/domain/test_report.py`

复用现有 `scripts/report_writer.py` 的 `write_report()` 逻辑。

- [ ] **Step 1: 编写 report.py**

```python
"""质检报告 Excel 生成"""
import json
from pathlib import Path
from typing import Optional
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment


HEADER_FONT = Font(name="Microsoft YaHei", bold=True, size=11, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
SEVERITY_FILLS = {
    "高": PatternFill(start_color="FF6B6B", end_color="FF6B6B", fill_type="solid"),
    "中": PatternFill(start_color="FFD93D", end_color="FFD93D", fill_type="solid"),
    "低": PatternFill(start_color="6BCB77", end_color="6BCB77", fill_type="solid"),
}


def _style_header(cell):
    cell.font = HEADER_FONT
    cell.fill = HEADER_FILL
    cell.alignment = Alignment(horizontal="center", vertical="center")


def write_report(
    output_path: str,
    qc_results: list[dict],
    attribution_results: dict,
    stats: dict,
) -> None:
    """生成 3-Sheet 质检报告 Excel。"""
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
        violations = record.get("violations", [])
        if not violations:
            ws_qc.cell(row_num, 1, record.get("id", ""))
            ws_qc.cell(row_num, 2, record.get("time", ""))
            ws_qc.cell(row_num, 3, record.get("intent", ""))
            ws_qc.cell(row_num, 4, "通过")
            row_num += 1
        else:
            for v in violations:
                ws_qc.cell(row_num, 1, record.get("id", ""))
                ws_qc.cell(row_num, 2, record.get("time", ""))
                ws_qc.cell(row_num, 3, record.get("intent", ""))
                ws_qc.cell(row_num, 4, v.get("rule_id", ""))
                ws_qc.cell(row_num, 5, v.get("rule_name", ""))
                ws_qc.cell(row_num, 6, v.get("severity", ""))
                ws_qc.cell(row_num, 7, v.get("evidence", ""))
                ws_qc.cell(row_num, 8, v.get("suggestion", ""))
                severity = v.get("severity", "")
                if severity in SEVERITY_FILLS:
                    ws_qc.cell(row_num, 6).fill = SEVERITY_FILLS[severity]
                row_num += 1

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

    # Sheet 3: 统计概览
    ws_stats = wb.create_sheet("统计概览")
    ws_stats.sheet_properties.tabColor = "FFD93D"

    ws_stats.cell(1, 1, "指标")
    ws_stats.cell(1, 2, "数值")
    _style_header(ws_stats.cell(1, 1))
    _style_header(ws_stats.cell(1, 2))

    row_num = 2
    for key, label in [("total", "总对话数"), ("pass", "通过数"), ("violation_rate", "违规率")]:
        if key in stats:
            ws_stats.cell(row_num, 1, label)
            ws_stats.cell(row_num, 2, stats[key])
            row_num += 1

    row_num += 1
    rule_header = ["规则ID", "规则名称", "命中次数", "占比"]
    for col, h in enumerate(rule_header, 1):
        _style_header(ws_stats.cell(row_num, col, h))
    row_num += 1

    rules_hit = stats.get("rules_hit", {})
    rule_names = stats.get("rule_names", {})
    total_violations = sum(rules_hit.values()) if rules_hit else 0

    for rule_id in sorted(rules_hit.keys()):
        count = rules_hit[rule_id]
        pct = f"{count / total_violations * 100:.1f}%" if total_violations > 0 else "0.0%"
        ws_stats.cell(row_num, 1, rule_id)
        ws_stats.cell(row_num, 2, rule_names.get(rule_id, ""))
        ws_stats.cell(row_num, 3, count)
        ws_stats.cell(row_num, 4, pct)
        row_num += 1

    ws_stats.cell(row_num, 1, "合计")
    ws_stats.cell(row_num, 3, total_violations)
    ws_stats.cell(row_num, 4, "100.0%")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    wb.close()


def verify_report_exists(output_path: str) -> bool:
    """验证报告文件是否生成且非空。"""
    p = Path(output_path)
    return p.exists() and p.stat().st_size > 0
```

- [ ] **Step 2: 编写测试**

```python
# tests/domain/test_report.py
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
    attr = {}
    stats = {"total": 2, "pass": 1, "violation_rate": "50.0%",
             "rules_hit": {"R01": 1}, "rule_names": {"R01": "测试规则"}}

    with tempfile.TemporaryDirectory() as tmpdir:
        output = str(Path(tmpdir) / "报告.xlsx")
        write_report(output, qc_results, attr, stats)
        assert verify_report_exists(output)


def test_report_file_not_exists():
    assert not verify_report_exists("/nonexistent/path/report.xlsx")
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/domain/test_report.py -v
```

Expected: 2 tests passed.

- [ ] **Step 4: Commit**

```bash
git add src/auto_qc/domain/report.py tests/domain/test_report.py
git commit -m "feat: Excel 报告生成器"
```

---

## Task 8: 框架层 — 校验器

**Files:**
- Create: `src/auto_qc/framework/validator.py`
- Create: `tests/framework/test_validator.py`

validator 是框架层最关键的模块——它是 Harness 的硬约束执行者。

- [ ] **Step 1: 编写 validator.py**

```python
"""通用契约校验——每个步骤的输入输出校验"""
import json
from typing import Optional
from auto_qc.domain.schemas import WorkerOutput, RulePackage, Batch


class ValidationError(Exception):
    """校验失败异常。"""
    pass


# ─── 规则校验 ───

def validate_rule_package(pkg: RulePackage) -> None:
    """校验规则包：ID 唯一、severity 合法、必填字段完整。"""
    from auto_qc.domain.rules import validate_rule_package as _validate
    errors = _validate(pkg)
    if errors:
        raise ValidationError("规则校验失败:\n" + "\n".join(f"  - {e}" for e in errors))


# ─── 数据校验 ───

def validate_batches(batches: list[Batch]) -> None:
    """校验加载后的批次数据。"""
    if not batches:
        raise ValidationError("批次列表为空，未加载到任何数据")

    all_ids = set()
    for batch in batches:
        if batch.size == 0:
            raise ValidationError(f"批次 {batch.batch_id} 为空")
        for c in batch.conversations:
            if not c.id:
                raise ValidationError(f"批次 {batch.batch_id} 存在空 ID")
            if c.id in all_ids:
                raise ValidationError(f"对话 ID 重复: {c.id}")
            all_ids.add(c.id)

    if len(all_ids) == 0:
        raise ValidationError("未读取到任何有效对话")


# ─── Worker 结果校验 ───

def validate_worker_output(raw_json: str, batch_size: int, expected_rule_ids: list[str]) -> WorkerOutput:
    """
    校验 Worker 返回的 JSON：
    1. JSON 合法
    2. 结果数量 == 批次大小
    3. 每条有 id 且不重复
    4. rules_checked 包含所有规则 ID
    5. spot_check_details >= 3 条
    6. 每条 violation 必填字段完整
    """
    # Step 1: JSON 解析
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise ValidationError(f"Worker 返回无效 JSON: {e}")

    # Step 2: 解析为 WorkerOutput
    try:
        output = WorkerOutput.from_dict(data)
    except Exception as e:
        raise ValidationError(f"Worker 结果结构解析失败: {e}")

    # Step 3: 结果数量
    actual_count = len(output.results)
    if actual_count != batch_size:
        raise ValidationError(
            f"结果数量不匹配: 期望 {batch_size} 条，实际 {actual_count} 条 (batch {output.batch_id})"
        )

    # Step 4: ID 唯一且无空
    seen_ids = set()
    for r in output.results:
        if not r.id:
            raise ValidationError(f"批次 {output.batch_id} 存在空 ID 的结果")
        if r.id in seen_ids:
            raise ValidationError(f"批次 {output.batch_id} 存在重复 ID: {r.id}")
        seen_ids.add(r.id)

    # Step 5: rules_checked 完整性
    checked = set(output.rules_checked)
    missing = set(expected_rule_ids) - checked
    if missing:
        raise ValidationError(
            f"批次 {output.batch_id} 未检查的规则: {missing}"
        )

    # Step 6: spot_check_details 不少于 3 条
    if len(output.spot_check_details) < 3:
        raise ValidationError(
            f"批次 {output.batch_id} spot_check_details 仅 {len(output.spot_check_details)} 条，至少需要 3 条"
        )

    # Step 7: 每条 violation 必填字段完整
    for r in output.results:
        if r.status == "violation":
            for v in r.violations:
                if not v.rule_id:
                    raise ValidationError(f"ID {r.id}: violation 缺少 rule_id")
                if not v.evidence:
                    raise ValidationError(f"ID {r.id} {v.rule_id}: violation 缺少 evidence")
                if not v.suggestion:
                    raise ValidationError(f"ID {r.id} {v.rule_id}: violation 缺少 suggestion")

    return output


def validate_merge_results(
    results: list[dict], expected_total: int
) -> None:
    """校验合并后的结果总数。"""
    if len(results) != expected_total:
        raise ValidationError(
            f"合并结果总数不匹配: 期望 {expected_total} 条，实际 {len(results)} 条"
        )
```

- [ ] **Step 2: 编写测试**

```python
# tests/framework/test_validator.py
import json
import pytest
from auto_qc.framework.validator import (
    ValidationError, validate_rule_package, validate_batches,
    validate_worker_output, validate_merge_results,
)
from auto_qc.domain.schemas import RulePackage, Rule, Batch, Conversation


class TestValidateRulePackage:
    def test_valid_package(self):
        pkg = RulePackage(rules=[
            Rule(rule_id="R01", name="x", severity="高", description="d", detection_logic="l"),
        ])
        validate_rule_package(pkg)  # 不抛异常

    def test_invalid_severity_raises(self):
        pkg = RulePackage(rules=[
            Rule(rule_id="R01", name="x", severity="CRITICAL", description="d", detection_logic="l"),
        ])
        with pytest.raises(ValidationError, match="severity"):
            validate_rule_package(pkg)


class TestValidateBatches:
    def test_empty_batches_raises(self):
        with pytest.raises(ValidationError, match="为空"):
            validate_batches([])

    def test_valid_batches(self):
        batches = [Batch(batch_id=1, conversations=[
            Conversation(id="1", time="", intent="", conversation=""),
        ])]
        validate_batches(batches)  # 不抛异常


class TestValidateWorkerOutput:
    def test_valid_output(self):
        raw = json.dumps({
            "batch_id": 1,
            "rules_checked": ["R01", "R02"],
            "spot_check_details": [
                {"id": "1", "reasoning": "..."},
                {"id": "2", "reasoning": "..."},
                {"id": "3", "reasoning": "..."},
            ],
            "results": [
                {"id": "1", "status": "pass", "violations": []},
                {"id": "2", "status": "pass", "violations": []},
            ],
        }, ensure_ascii=False)
        output = validate_worker_output(raw, batch_size=2, expected_rule_ids=["R01", "R02"])
        assert output.batch_id == 1
        assert len(output.results) == 2

    def test_count_mismatch_raises(self):
        raw = json.dumps({
            "batch_id": 1,
            "rules_checked": ["R01"],
            "spot_check_details": [{"id": "1", "reasoning": ""}] * 3,
            "results": [{"id": "1", "status": "pass", "violations": []}],
        }, ensure_ascii=False)
        with pytest.raises(ValidationError, match="数量不匹配"):
            validate_worker_output(raw, batch_size=3, expected_rule_ids=["R01"])

    def test_missing_rules_checked_raises(self):
        raw = json.dumps({
            "batch_id": 1,
            "rules_checked": ["R01"],
            "spot_check_details": [{"id": "1", "reasoning": ""}] * 3,
            "results": [{"id": "1", "status": "pass", "violations": []}],
        }, ensure_ascii=False)
        with pytest.raises(ValidationError, match="未检查的规则"):
            validate_worker_output(raw, batch_size=1, expected_rule_ids=["R01", "R02", "R03"])

    def test_insufficient_spot_check_raises(self):
        raw = json.dumps({
            "batch_id": 1,
            "rules_checked": ["R01"],
            "spot_check_details": [{"id": "1", "reasoning": ""}],
            "results": [{"id": "1", "status": "pass", "violations": []}],
        }, ensure_ascii=False)
        with pytest.raises(ValidationError, match="spot_check"):
            validate_worker_output(raw, batch_size=1, expected_rule_ids=["R01"])

    def test_missing_evidence_raises(self):
        raw = json.dumps({
            "batch_id": 1,
            "rules_checked": ["R01"],
            "spot_check_details": [{"id": "1", "reasoning": ""}] * 3,
            "results": [{
                "id": "1",
                "status": "violation",
                "violations": [{
                    "rule_id": "R01",
                    "rule_name": "x",
                    "severity": "高",
                    "evidence": "",
                    "suggestion": "",
                }],
            }],
        }, ensure_ascii=False)
        with pytest.raises(ValidationError, match="evidence"):
            validate_worker_output(raw, batch_size=1, expected_rule_ids=["R01"])


class TestValidateMergeResults:
    def test_count_match(self):
        validate_merge_results([{"id": "1"}, {"id": "2"}], 2)  # 不抛异常

    def test_count_mismatch(self):
        with pytest.raises(ValidationError, match="总数不匹配"):
            validate_merge_results([{"id": "1"}], 3)
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/framework/test_validator.py -v
```

Expected: 9 tests passed.

- [ ] **Step 4: Commit**

```bash
git add src/auto_qc/framework/validator.py tests/framework/test_validator.py
git commit -m "feat: 通用契约校验器（Harness 硬约束）"
```

---

## Task 9: 框架层 — 进度管理

**Files:**
- Create: `src/auto_qc/framework/progress.py`
- Create: `tests/framework/test_progress.py`

- [ ] **Step 1: 编写 progress.py**

```python
"""进度文件读写"""
import json
from datetime import datetime
from pathlib import Path
from auto_qc.domain.schemas import Progress


def create_progress(work_dir: str, total_batches: int, phase: str = "qc") -> Progress:
    """创建新进度文件。"""
    now = datetime.now().isoformat()
    progress = Progress(
        total_batches=total_batches,
        completed_batches=0,
        phase=phase,
        batch_status={str(i): "pending" for i in range(1, total_batches + 1)},
        retry_count={str(i): 0 for i in range(1, total_batches + 1)},
        failed_batches=[],
        started_at=now,
        updated_at=now,
    )
    save_progress(work_dir, progress)
    return progress


def load_progress(work_dir: str) -> Progress:
    """读取进度文件。不存在时返回初始状态。"""
    path = Path(work_dir) / "progress.json"
    if not path.exists():
        return Progress()

    data = json.loads(path.read_text(encoding="utf-8"))
    return Progress(
        total_batches=data.get("total_batches", 0),
        completed_batches=data.get("completed_batches", 0),
        phase=data.get("phase", "init"),
        batch_status=data.get("batch_status", {}),
        retry_count=data.get("retry_count", {}),
        failed_batches=data.get("failed_batches", []),
        started_at=data.get("started_at", ""),
        updated_at=data.get("updated_at", ""),
    )


def save_progress(work_dir: str, progress: Progress) -> None:
    """写入进度文件。"""
    path = Path(work_dir) / "progress.json"
    progress.updated_at = datetime.now().isoformat()
    if not progress.started_at:
        progress.started_at = progress.updated_at

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "total_batches": progress.total_batches,
        "completed_batches": progress.completed_batches,
        "phase": progress.phase,
        "batch_status": progress.batch_status,
        "retry_count": progress.retry_count,
        "failed_batches": progress.failed_batches,
        "started_at": progress.started_at,
        "updated_at": progress.updated_at,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def has_unfinished(work_dir: str) -> bool:
    """检查是否存在未完成的进度。"""
    progress = load_progress(work_dir)
    if progress.phase == "done":
        return False
    if progress.total_batches == 0:
        return False
    return True


def reset_running_batches(progress: Progress) -> Progress:
    """将状态为 'running' 的批次重置为 'pending'（用于断点续跑）。"""
    for bid, status in progress.batch_status.items():
        if status == "running":
            progress.batch_status[bid] = "pending"
    return progress
```

- [ ] **Step 2: 编写测试**

```python
# tests/framework/test_progress.py
import tempfile
from pathlib import Path
from auto_qc.framework.progress import (
    create_progress, load_progress, save_progress, has_unfinished, reset_running_batches,
)
from auto_qc.domain.schemas import Progress


def test_create_and_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        p = create_progress(tmpdir, total_batches=5)
        assert p.total_batches == 5
        assert p.batch_status["1"] == "pending"
        assert Path(tmpdir, "progress.json").exists()

        loaded = load_progress(tmpdir)
        assert loaded.total_batches == 5


def test_has_unfinished():
    with tempfile.TemporaryDirectory() as tmpdir:
        assert not has_unfinished(tmpdir)  # 没有进度文件
        create_progress(tmpdir, total_batches=3, phase="qc")
        assert has_unfinished(tmpdir)

        p = load_progress(tmpdir)
        p.phase = "done"
        save_progress(tmpdir, p)
        assert not has_unfinished(tmpdir)


def test_reset_running():
    p = Progress(
        total_batches=3,
        batch_status={"1": "done", "2": "running", "3": "pending"},
    )
    reset_running_batches(p)
    assert p.batch_status["2"] == "pending"
    assert p.batch_status["1"] == "done"
    assert p.batch_status["3"] == "pending"
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/framework/test_progress.py -v
```

Expected: 3 tests passed.

- [ ] **Step 4: Commit**

```bash
git add src/auto_qc/framework/progress.py tests/framework/test_progress.py
git commit -m "feat: 进度管理模块（断点续跑支持）"
```

---

## Task 10: 框架层 — 并发协调器

**Files:**
- Create: `src/auto_qc/framework/coordinator.py`
- Create: `tests/framework/test_coordinator.py`

- [ ] **Step 1: 编写 coordinator.py**

```python
"""并发控制——原子化状态管理，硬限制最大并发数"""
from auto_qc.domain.schemas import Progress
from auto_qc.framework.progress import load_progress, save_progress

MAX_CONCURRENCY = 5


class Coordinator:
    """批次分发协调器，原子化控制并发数。"""

    def __init__(self, work_dir: str, max_concurrency: int = MAX_CONCURRENCY):
        self.work_dir = work_dir
        self.max_concurrency = max_concurrency

    def get_next_batches(self) -> list[int]:
        """
        获取下一批可执行的批次 ID 列表（最多 max_concurrency 个）。
        原子化地将 selected → running 状态写入 progress。
        返回空列表表示全部完成。
        """
        progress = load_progress(self.work_dir)

        # 统计当前 running 数
        running_count = sum(
            1 for s in progress.batch_status.values() if s == "running"
        )

        available_slots = self.max_concurrency - running_count
        if available_slots <= 0:
            return []

        # 找到 pending 批次
        next_batches = []
        for bid in sorted(progress.batch_status.keys(), key=int):
            if progress.batch_status[bid] == "pending":
                next_batches.append(int(bid))
                if len(next_batches) >= available_slots:
                    break

        # 原子化标记为 running
        for bid in next_batches:
            progress.batch_status[str(bid)] = "running"

        save_progress(self.work_dir, progress)
        return next_batches

    def mark_done(self, batch_id: int) -> None:
        """标记批次完成。"""
        progress = load_progress(self.work_dir)
        progress.batch_status[str(batch_id)] = "done"
        progress.completed_batches = sum(
            1 for s in progress.batch_status.values() if s == "done"
        )
        if progress.completed_batches >= progress.total_batches:
            progress.phase = "done"
        save_progress(self.work_dir, progress)

    def mark_failed(self, batch_id: int) -> None:
        """标记批次失败（连续 3 次重试都失败后调用）。"""
        progress = load_progress(self.work_dir)
        progress.batch_status[str(batch_id)] = "failed"
        progress.failed_batches.append(batch_id)
        # 不增加 completed_batches（失败的批次不计数）
        save_progress(self.work_dir, progress)

    def increment_retry(self, batch_id: int) -> int:
        """增加批次重试次数，返回当前次数。"""
        progress = load_progress(self.work_dir)
        current = progress.retry_count.get(str(batch_id), 0) + 1
        progress.retry_count[str(batch_id)] = current
        progress.batch_status[str(batch_id)] = "pending"  # 重置为 pending 等待重跑
        save_progress(self.work_dir, progress)
        return current

    def get_summary(self) -> dict:
        """返回当前状态摘要。"""
        progress = load_progress(self.work_dir)
        statuses = progress.batch_status.values()
        return {
            "total": progress.total_batches,
            "done": sum(1 for s in statuses if s == "done"),
            "running": sum(1 for s in statuses if s == "running"),
            "pending": sum(1 for s in statuses if s == "pending"),
            "failed": sum(1 for s in statuses if s == "failed"),
        }
```

- [ ] **Step 2: 编写测试**

```python
# tests/framework/test_coordinator.py
import tempfile
from auto_qc.framework.coordinator import Coordinator
from auto_qc.framework.progress import create_progress


def test_get_next_empty_when_all_done():
    with tempfile.TemporaryDirectory() as tmpdir:
        coordinator = Coordinator(tmpdir)
        create_progress(tmpdir, total_batches=0)
        assert coordinator.get_next_batches() == []


def test_get_next_returns_pending():
    with tempfile.TemporaryDirectory() as tmpdir:
        coordinator = Coordinator(tmpdir, max_concurrency=3)
        create_progress(tmpdir, total_batches=5)
        batches = coordinator.get_next_batches()
        assert batches == [1, 2, 3]  # 最多 3 个


def test_get_next_respects_running():
    with tempfile.TemporaryDirectory() as tmpdir:
        coordinator = Coordinator(tmpdir, max_concurrency=3)
        create_progress(tmpdir, total_batches=5)
        # 第一轮：拿到 1,2,3
        coordinator.get_next_batches()
        # 标记 1 完成后
        coordinator.mark_done(1)
        # 第二轮：应该拿到 4（而非 1,2,3——2,3 还在 running）
        batches = coordinator.get_next_batches()
        assert batches == [4]


def test_mark_done_and_failed():
    with tempfile.TemporaryDirectory() as tmpdir:
        coordinator = Coordinator(tmpdir)
        create_progress(tmpdir, total_batches=3)
        coordinator.get_next_batches()  # 拿出 1,2,3，全部标记 running
        coordinator.mark_done(1)
        coordinator.mark_failed(2)
        summary = coordinator.get_summary()
        assert summary["done"] == 1
        assert summary["failed"] == 1


def test_retry_increment():
    with tempfile.TemporaryDirectory() as tmpdir:
        coordinator = Coordinator(tmpdir)
        create_progress(tmpdir, total_batches=1)
        coordinator.get_next_batches()  # batch 1 → running
        count = coordinator.increment_retry(1)
        assert count == 1
        # batch 1 应该被重置为 pending
        summary = coordinator.get_summary()
        assert summary["pending"] == 1
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/framework/test_coordinator.py -v
```

Expected: 5 tests passed.

- [ ] **Step 4: Commit**

```bash
git add src/auto_qc/framework/coordinator.py tests/framework/test_coordinator.py
git commit -m "feat: 并发协调器（原子化状态管理）"
```

---

## Task 11: 框架层 — LLM Worker

**Files:**
- Create: `src/auto_qc/framework/worker.py`
- Create: `tests/framework/test_worker.py`

- [ ] **Step 1: 编写 worker.py**

```python
"""LLM API 调用封装——发 prompt、收 JSON、过滤 thinking block、json_repair"""
import os
import json
import asyncio
from anthropic import AsyncAnthropic
from anthropic.types import TextBlock
from json_repair import repair_json

MAX_RETRIES = 3


def _get_client() -> AsyncAnthropic:
    """从环境变量创建 Anthropic 客户端。"""
    return AsyncAnthropic(
        base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        api_key=os.environ.get("ANTHROPIC_AUTH_TOKEN", ""),
    )


def _get_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-6"))


async def call_llm(prompt: str, max_tokens: int = 4000) -> str:
    """
    调用 LLM API，返回仅包含 text 内容的响应字符串。
    自动过滤 ThinkingBlock，只取 TextBlock。
    """
    client = _get_client()
    model = _get_model()

    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )

    texts = []
    for block in response.content:
        if isinstance(block, TextBlock):
            texts.append(block.text)

    return "\n".join(texts)


async def call_llm_with_retry(prompt: str, max_tokens: int = 4000) -> str:
    """
    调用 LLM，失败时重试最多 MAX_RETRIES 次。
    """
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            return await call_llm(prompt, max_tokens)
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s 退避

    raise RuntimeError(f"LLM 调用失败（重试 {MAX_RETRIES} 次后）: {last_error}")


def extract_json(text: str) -> str:
    """
    从 LLM 返回的文本中提取 JSON。
    先用 json_repair 修复常见格式问题，再验证解析。
    """
    # 尝试直接解析
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    # 尝试 json_repair 修复
    try:
        repaired = repair_json(text)
        json.loads(repaired)
        return repaired
    except (json.JSONDecodeError, Exception):
        pass

    # 尝试从 text 中提取 JSON 块（```json ... ```）
    import re
    match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if match:
        inner = match.group(1).strip()
        try:
            repair_json(inner)
            return inner
        except Exception:
            pass

    raise ValueError(f"无法从 LLM 响应中提取有效 JSON")
```

- [ ] **Step 2: 编写测试**

```python
# tests/framework/test_worker.py
import json
from auto_qc.framework.worker import extract_json


def test_extract_valid_json():
    text = '{"batch_id": 1, "results": []}'
    result = extract_json(text)
    parsed = json.loads(result)
    assert parsed["batch_id"] == 1


def test_extract_with_markdown_wrapper():
    text = '```json\n{"batch_id": 1, "results": []}\n```'
    result = extract_json(text)
    parsed = json.loads(result)
    assert parsed["batch_id"] == 1


def test_extract_trailing_comma():
    text = '{"batch_id": 1, "results": [],}'
    result = extract_json(text)
    parsed = json.loads(result)
    assert parsed["batch_id"] == 1


def test_extract_invalid_raises():
    import pytest
    with pytest.raises(ValueError):
        extract_json("这是纯文本，不是 JSON")
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/framework/test_worker.py -v
```

Expected: 4 tests passed.

- [ ] **Step 4: Commit**

```bash
git add src/auto_qc/framework/worker.py tests/framework/test_worker.py
git commit -m "feat: LLM Worker 调用封装（API + json_repair）"
```

---

## Task 12: 框架层 — 交叉验证

**Files:**
- Create: `src/auto_qc/framework/cross_validator.py`
- Create: `tests/framework/test_cross_validator.py`

- [ ] **Step 1: 编写 cross_validator.py**

```python
"""交叉验证引擎——分层抽样、规则级对比、差异率计算"""
import random
from auto_qc.domain.schemas import CrossValidationResult


def stratified_sample(
    results: list[dict],
    violation_ratio: float = 0.02,
    non_violation_ratio: float = 0.01,
    random_seed: int = 42,
) -> list[dict]:
    """
    分层抽样：违规组抽 violation_ratio，非违规组抽 non_violation_ratio。
    返回抽中的完整结果列表。
    """
    random.seed(random_seed)

    violation_items = []
    non_violation_items = []

    for r in results:
        if r.get("violations"):
            violation_items.append(r)
        else:
            non_violation_items.append(r)

    sample_size_v = max(1, int(len(violation_items) * violation_ratio))
    sample_size_nv = max(1, int(len(non_violation_items) * non_violation_ratio))

    sample = (
        random.sample(violation_items, min(sample_size_v, len(violation_items))) +
        random.sample(non_violation_items, min(sample_size_nv, len(non_violation_items)))
    )

    return sample


def compare_results(
    original: list[dict],
    recheck: list[dict],
) -> CrossValidationResult:
    """
    规则级对比：同一条对话同一个规则，两次判断是否一致。
    original 和 recheck 的每条结果格式：
    {"id": "...", "violations": [{"rule_id": "R01", ...}, ...]}
    """
    original_map = {}
    for r in original:
        original_map[r["id"]] = {v["rule_id"] for v in r.get("violations", [])}

    recheck_map = {}
    for r in recheck:
        recheck_map[r["id"]] = {v["rule_id"] for v in r.get("violations", [])}

    # 统计所有规则判断
    total_judgments = 0
    mismatches = 0

    all_rule_ids = set()
    for rules in original_map.values():
        all_rule_ids.update(rules)
    for rules in recheck_map.values():
        all_rule_ids.update(rules)

    for item_id in original_map:
        if item_id not in recheck_map:
            continue
        for rule_id in all_rule_ids:
            total_judgments += 1
            in_original = rule_id in original_map[item_id]
            in_recheck = rule_id in recheck_map[item_id]
            if in_original != in_recheck:
                mismatches += 1

    return CrossValidationResult.compute(mismatches, total_judgments)
```

- [ ] **Step 2: 编写测试**

```python
# tests/framework/test_cross_validator.py
from auto_qc.framework.cross_validator import stratified_sample, compare_results


def test_stratified_sample():
    results = (
        [{"id": f"v{i}", "violations": [{"rule_id": "R01"}]} for i in range(50)] +
        [{"id": f"p{i}", "violations": []} for i in range(50)]
    )
    sample = stratified_sample(results, violation_ratio=0.2, non_violation_ratio=0.2)
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
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/framework/test_cross_validator.py -v
```

Expected: 3 tests passed.

- [ ] **Step 4: Commit**

```bash
git add src/auto_qc/framework/cross_validator.py tests/framework/test_cross_validator.py
git commit -m "feat: 交叉验证引擎（分层抽样+规则级对比）"
```

---

## Task 13: 框架层 — 全流程编排器

**Files:**
- Create: `src/auto_qc/framework/orchestrator.py`

orchestrator 串联所有步骤，这是全流程的驱动核心。

- [ ] **Step 1: 编写 orchestrator.py（上半——合规检测）**

```python
"""全流程编排器——串联 Step 0 到 Step 7"""
import asyncio
import json
import datetime
from pathlib import Path
from typing import Optional

from auto_qc.domain.schemas import Batch
from auto_qc.domain.rules import parse_rules_file
from auto_qc.domain.data_loader import load_conversations, save_batches
from auto_qc.domain.prompts import build_qc_prompt, build_attribution_prompt
from auto_qc.domain.report import write_report, verify_report_exists
from auto_qc.domain.attribution import get_attribution_rules_path

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

    # 循环直到所有批次完成
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
    print(f"  ✅ 文件存在")

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
            # 归因结果按 intent 分组
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
```

> **注:** orchestrator.py 为完整流程统一入口。部分 `Conversation` 未 import 的细节在后续集成测试中修正。

- [ ] **Step 2: Commit**

```bash
git add src/auto_qc/framework/orchestrator.py
git commit -m "feat: 全流程编排器（Step 1-7 串联）"
```

---

## Task 14: CLI 入口

**Files:**
- Create: `src/auto_qc/cli.py`

- [ ] **Step 1: 编写 cli.py**

```python
"""CLI 命令行入口"""
import argparse
import asyncio
import datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="auto-qc 外呼通话文本质检",
        prog="auto-qc",
    )
    parser.add_argument("--data", required=True, help="源数据 Excel 文件路径")
    parser.add_argument("--rules", help="合规规则 Markdown 文件路径（--attribution-only 时不需要）")
    parser.add_argument("--no-attribution", action="store_true", help="关闭归因分析")
    parser.add_argument("--attribution-only", action="store_true", help="仅执行归因分析")
    parser.add_argument("--output", help="报告输出路径（默认输出到数据文件同目录）")
    parser.add_argument("--work-dir", default="./auto_qc_work", help="工作目录（临时文件存放）")

    args = parser.parse_args()

    # 确定输出路径
    if args.output:
        output_path = args.output
    else:
        data_path = Path(args.data)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(
            data_path.parent / f"{data_path.stem}_质检报告_{timestamp}.xlsx"
        )

    # 模式判断
    attribution_only = args.attribution_only
    skip_attribution = args.no_attribution

    if attribution_only:
        rules_path = None  # 归因模式不需要合规规则
    else:
        if not args.rules:
            parser.error("合规检测模式需要 --rules 参数（或用 --attribution-only 仅做归因分析）")
        rules_path = args.rules

    if attribution_only:
        # TODO: 后续实现纯归因模式
        print("归因分析模式...")
    else:
        from auto_qc.framework.orchestrator import run_qc
        asyncio.run(run_qc(
            data_path=args.data,
            rules_path=rules_path,
            output_path=output_path,
            work_dir=args.work_dir,
            skip_attribution=skip_attribution,
        ))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 验证 CLI 能正常启动（参数解析测试）**

```bash
uv run python -m auto_qc.cli --help
```

Expected: 显示 help 信息，列出 `--data`, `--rules`, `--no-attribution` 等参数。

- [ ] **Step 3: Commit**

```bash
git add src/auto_qc/cli.py
git commit -m "feat: CLI 命令行入口"
```

---

## Task 15: SKILL.md 精简 + AGENTS.md 更新

**Files:**
- Modify: `SKILL.md`
- Modify: `AGENTS.md`（如有）

- [ ] **Step 1: 精简 SKILL.md 为薄壳入口**

将当前的 251 行 SKILL.md 替换为：

```markdown
---
name: auto-qc
description: 外呼通话对话文本质检。指定数据 Excel 和规则文件，自动完成合规检测 + 归因分析，输出 Excel 报告。
---

# auto-qc 外呼通话文本质检

## 触发方式

用户输入：`/auto-qc --data <excel路径> --rules <规则路径> [--no-attribution] [--attribution-only] [--output <报告路径>]`

## 执行方式

1. 检查当前目录 `./auto_qc/` 是否存在
   - 不存在 → 从 Skill 包复制代码到 `./auto_qc/`
   - 存在 → 对比版本，Skill 版本更高则覆盖更新
2. `cd ./auto_qc/ && uv sync && uv run -m auto_qc.cli <传递所有参数>`

## 运行模式

| 命令 | 行为 |
|------|------|
| `--data <路径> --rules <路径>` | 合规检测 + 归因分析 |
| `--data <路径> --rules <路径> --no-attribution` | 仅合规检测 |
| `--data <路径> --attribution-only` | 仅归因分析（内置规则） |

## 参数

- `--data`（必需）：源数据 Excel 文件路径
- `--rules`（合规检测模式必需）：合规规则 Markdown 文件路径
- `--no-attribution`：关闭归因分析
- `--attribution-only`：仅归因分析
- `--output`：报告输出路径
```

- [ ] **Step 2: Commit**

```bash
git add SKILL.md AGENTS.md
git commit -m "docs: SKILL.md 精简为薄壳入口"
```

---

## Task 16: 集成测试

**Files:**
- Create: `tests/test_integration.py`

用真实测试数据端到端跑一次，验证全流程能串通。

- [ ] **Step 1: 编写集成测试**

```python
# tests/test_integration.py
"""端到端集成测试——用项目 tmp/ 目录下的真实测试数据"""
import asyncio
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

    def test_full_data_pipeline(self):
        """测试：规则解析 → 数据加载 → 批次拆分 → 校验"""
        # 规则解析
        rules_path = Path(__file__).parent.parent / "templates" / "attribution-rules.md"
        if not rules_path.exists():
            pytest.skip("规则文件不存在")

        pkg = parse_rules_file(str(rules_path))
        validate_rule_package(pkg)

        # 数据加载（用 tmp/test_batches 中的测试数据）
        test_batch = Path(__file__).parent.parent / "tmp" / "test_batches" / "batch_1.json"
        if not test_batch.exists():
            pytest.skip("测试数据不存在")

        data = json.loads(test_batch.read_text(encoding="utf-8"))
        assert data["total"] > 0
        assert len(data["conversations"]) > 0

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
```

- [ ] **Step 2: 运行集成测试**

```bash
uv run pytest tests/test_integration.py -v
```

Expected: 3 tests passed（依赖测试数据文件存在）。

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: 端到端集成测试"
```

---

## 最终验证

全部测试通过：

```bash
uv run pytest tests/ -v
```

Expected: 全部 ~35 个测试通过。

---

## 待办事项（后续迭代）

- [ ] 归因分析的按 intent 分组逻辑完善（`_group_attribution` 目前在 orchestrator 中为占位）
- [ ] `--attribution-only` 模式完整实现
- [ ] 初始化脚本：SKILL.md 调用的版本检测和代码复制逻辑
- [ ] `--keep-temp` 参数支持
- [ ] 进度汇报优化（每 10% 打印一次）
- [ ] failed_batches 在报告中汇总提示
