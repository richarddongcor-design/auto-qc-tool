"""Prompt 模板组装——把规则 + 对话拼成 LLM 可理解的完整 prompt"""
import json
import re
from pathlib import Path
from auto_qc.domain.schemas import RulePackage, Batch, Rule

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


def clean_conversation_text(text: str) -> str:
    """
    清洗对话文本，在送入 LLM 前预处理：

    1. 移除 TTS 特殊符号（* ~ - 等用于语气/停顿控制的字符）
    2. 去重连续相同的 AI 发言（断句补齐导致的重复）
    3. 合并连续的用户发言（断句补齐，两条有关联的发言合为一条）
    """
    if not text:
        return text

    lines = text.split("\n")
    cleaned = []

    i = 0
    while i < len(lines):
        line = lines[i]
        # 移除 TTS 特殊符号
        line = re.sub(r"[*~\-]", "", line)
        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        if stripped.startswith("AI:"):
            # 去重连续相同的 AI 发言
            if cleaned and cleaned[-1] == stripped:
                i += 1
                continue
            cleaned.append(stripped)

        elif stripped.startswith("用户:"):
            # 合并连续的用户发言
            merged = stripped
            while i + 1 < len(lines):
                next_line = re.sub(r"[*~\-]", "", lines[i + 1]).strip()
                if next_line.startswith("用户:"):
                    merged = merged.rstrip("。，！？；,.!?;") + "，" + next_line[len("用户:"):].strip()
                    i += 1
                else:
                    break
            cleaned.append(merged)

        else:
            cleaned.append(stripped)

        i += 1

    return "\n".join(cleaned)


def build_single_rule_prompt(batch: Batch, rule: Rule) -> str:
    """
    组装单规则质检 prompt。
    每条规则独立构建一次 prompt，只包含一条规则的描述。
    """
    template = _load_template("worker-prompt.md")

    conversations_json = json.dumps(
        [{"id": c.id, "time": c.time, "intent": c.intent,
          "conversation": clean_conversation_text(c.conversation)}
         for c in batch.conversations],
        ensure_ascii=False,
        indent=2,
    )

    rule_json = json.dumps(
        {"rule_id": rule.rule_id, "name": rule.name, "severity": rule.severity,
         "description": rule.description, "detection_logic": rule.detection_logic,
         "examples": rule.examples},
        ensure_ascii=False,
        indent=2,
    )

    prompt = template.replace("{{RULE_JSON}}", rule_json)
    prompt = prompt.replace("{{RULE_ID}}", rule.rule_id)
    prompt = prompt.replace("{{BATCH_SIZE}}", str(batch.size))
    prompt = prompt.replace("{{CONVERSATIONS}}", conversations_json)
    prompt = prompt.replace("{{BATCH_ID}}", str(batch.batch_id))

    return prompt
