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

    conversations_json = json.dumps(
        [{"id": c.id, "time": c.time, "intent": c.intent, "conversation": c.conversation}
         for c in batch.conversations],
        ensure_ascii=False,
        indent=2,
    )

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
