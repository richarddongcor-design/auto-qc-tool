"""阶段完成报告和最终 rules.md 生成。"""

from pathlib import Path
from datetime import datetime


def generate_rules_md(
    rules: list[dict],
    verification_results: list[dict],
    metadata: dict,
    run_dir: Path,
) -> str:
    """生成最终的 rules.md 文件。

    按验证状态过滤：
    - valid → 写入 rules.md，标注验证通过
    - 未验证 → 写入 rules.md，标注"未验证"
    - over_broad/too_narrow → 不写入 rules.md
    """
    run_id = metadata.get("run_id", "unknown")
    data_source = metadata.get("data_source", "unknown")
    total_dialogues = metadata.get("total_dialogues", 0)
    generated_at = datetime.now().strftime("%Y-%m-%d")

    # 构建验证结果查找表
    verification_map = {vr.get("rule_id", ""): vr for vr in verification_results}

    # 按验证状态过滤规则
    accepted_rules = []
    discarded_rules = []

    for rule in rules:
        rule_id = rule.get("rule_id", "")
        vr = verification_map.get(rule_id)

        if vr is None:
            # 未验证的规则，保留但标注
            accepted_rules.append((rule, "unverified"))
        elif vr.get("status") == "valid":
            accepted_rules.append((rule, "verified"))
        elif vr.get("status") == "too_narrow":
            discarded_rules.append((rule, vr))

    # 写入 rules.md
    lines = [
        "# AI 对话质检规则",
        "",
        "---",
        "",
    ]

    for rule, ver_status in accepted_rules:
        vr = verification_map.get(rule.get("rule_id", ""))
        confidence = rule.get("confidence", "unknown").upper()
        depth = rule.get("depth", "unknown")
        hit_rate = rule.get("hit_rate_estimate", "未知")
        support = rule.get("support_count", 0)

        # 深度翻译
        depth_cn = {"superficial": "🟡 浅层", "moderate": "🟠 中层", "deep": "🔴 深层"}.get(depth, f"❓ {depth}")

        lines.extend([
            f"## {rule.get('rule_id', 'RULE-???')}: {rule.get('rule_name', '未命名')}",
            "",
            f"**置信度**: {confidence}",
            f"**深度**: {depth_cn}",
            f"**命中率**: {hit_rate} (估计)",
            f"**支持对话**: {support} 条",
        ])

        # 标注验证状态
        if ver_status == "verified" and vr:
            status_icon = "✅"
            tc = vr.get("total_checked", 0)
            lines.append(f"**验证状态**: {status_icon} valid (命中率: {vr.get('hit_rate', 'N/A'):.2%}, 验证对话数: {tc})")
        elif ver_status == "unverified":
            lines.append("**验证状态**: ⏳ 未验证（该规则未在验证样本中覆盖）")

        lines.extend([
            "",
            f"**描述**: {rule.get('description', '无描述')}",
            "",
            f"**检测逻辑**: {rule.get('detection_logic', '无检测逻辑')}",
            "",
        ])

        lines.append("---")
        lines.append("")

    rules_md = "\n".join(lines)
    (run_dir / "phase6").mkdir(parents=True, exist_ok=True)
    (run_dir / "phase6" / "rules.md").write_text(rules_md, encoding="utf-8")

    # 写入 rules_discarded.txt
    if discarded_rules:
        disc_lines = [
            "# 被过滤的规则",
            "",
            f"生成时间: {generated_at}",
            f"以下规则因验证未通过（命中率过低）被过滤，供人审阅参考。",
            "",
        ]
        for rule, vr in discarded_rules:
            status = vr.get("status", "unknown")
            hit_rate = vr.get("hit_rate", 0)
            disc_lines.extend([
                f"## {rule.get('rule_id', 'RULE-???')}: {rule.get('rule_name', '未命名')}",
                f"  验证状态: {status} (命中率: {hit_rate:.2%})",
                f"  原因: {vr.get('adjustment_suggestion', '无')}",
                f"  原始描述: {rule.get('description', '无')}",
                "",
            ])
        (run_dir / "phase6" / "rules_discarded.txt").write_text("\n".join(disc_lines), encoding="utf-8")

    return rules_md


def generate_rules_summary(
    rules: list[dict],
    metadata: dict,
    run_dir: Path,
) -> str:
    """生成简洁的规则摘要（供人快速阅读）。

    输出文件: phase6/rules_summary.txt
    内容: 本次运行发现了哪些问题类型、各有多少条、命中率。
    """
    total_dialogues = metadata.get("total_dialogues", 0)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    run_id = metadata.get("run_id", "unknown")

    lines = [
        f"[{generated_at}] AI 对话问题挖掘结果 | 运行 ID: {run_id}",
        f"数据来源: {metadata.get('data_source', '?')} ({total_dialogues} 条对话)",
        f"共发现 {len(rules)} 类问题",
        "",
    ]

    # 按置信度 + 命中率排序
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    sorted_rules = sorted(
        rules,
        key=lambda r: (
            confidence_order.get(r.get("confidence", "low"), 2),
            -r.get("support_count", 0),
        ),
    )

    for rule in sorted_rules:
        name = rule.get("rule_name", "?")
        confidence = rule.get("confidence", "?").upper()
        depth = rule.get("depth", "?")
        support = rule.get("support_count", 0)
        hit_rate = rule.get("hit_rate_estimate", "?")
        desc = rule.get("description", "")[:80]

        lines.append(f"  [{confidence}|{depth}] {name}")
        lines.append(f"    命中率: {hit_rate} | 支持对话: {support}/{total_dialogues} 条")
        if desc:
            lines.append(f"    {desc}")
        lines.append("")

    summary = "\n".join(lines)
    (run_dir / "phase6").mkdir(parents=True, exist_ok=True)
    (run_dir / "phase6" / "rules_summary.txt").write_text(summary, encoding="utf-8")
    return summary