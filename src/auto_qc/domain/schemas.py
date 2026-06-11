"""领域数据结构定义"""
from dataclasses import dataclass, field
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
class RuleSet:
    """一个规则集：命名的规则集合"""
    name: str               # 规则集标识符（如 "auto-pi"）
    display_name: str       # 用户可读标签（如 "人文角度"）
    description: str = ""   # 规则集说明
    rules: list[Rule] = field(default_factory=list)


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
    total_compared: int            # 对比的规则判断总数
    mismatches: int                # 不一致数
    discrepancy_rate: float        # 差异率
    status: str                    # "ok" | "suspicious" | "high"
    per_rule: dict = field(default_factory=dict)   # 每条规则的一致性/Kappa
    kappa: float = 0.0             # 总体 Cohen's Kappa
    kappa_status: str = "unknown"  # poor | fair | moderate | substantial | almost_perfect
    adjudicated_rules: list[str] = field(default_factory=list)  # 被裁决过的规则 ID

    @classmethod
    def compute(cls, mismatches: int, total: int, per_rule: dict = None) -> "CrossValidationResult":
        rate = mismatches / total if total > 0 else 0.0
        if rate < 0.05:
            status = "ok"
        elif rate < 0.10:
            status = "suspicious"
        else:
            status = "high"

        per_rule = per_rule or {}

        # 按规则判断次数加权平均 Kappa
        overall_kappa = 0.0
        if per_rule:
            weighted = sum(d["kappa"] * d["total_judgments"] for d in per_rule.values())
            total_w = sum(d["total_judgments"] for d in per_rule.values())
            overall_kappa = round(weighted / total_w, 4) if total_w > 0 else 0.0

        if overall_kappa >= 0.8:
            kappa_status = "almost_perfect"
        elif overall_kappa >= 0.6:
            kappa_status = "substantial"
        elif overall_kappa >= 0.4:
            kappa_status = "moderate"
        elif overall_kappa >= 0.2:
            kappa_status = "fair"
        else:
            kappa_status = "poor"

        return cls(
            total_compared=total,
            mismatches=mismatches,
            discrepancy_rate=rate,
            status=status,
            per_rule=per_rule,
            kappa=overall_kappa,
            kappa_status=kappa_status,
            adjudicated_rules=[],
        )


@dataclass
class Progress:
    """进度追踪"""
    total_batches: int = 0
    completed_batches: int = 0
    phase: str = "init"        # init | qc | cross_validation | reporting | done
    batch_status: dict[str, str] = field(default_factory=dict)   # "1": "done", ...
    retry_count: dict[str, int] = field(default_factory=dict)
    failed_batches: list[int] = field(default_factory=list)
    started_at: str = ""
    updated_at: str = ""
