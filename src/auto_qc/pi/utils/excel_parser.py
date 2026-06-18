"""Phase 1: Excel 数据解析 + chunk 切分。"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def split_into_chunks(dialogues: list[dict], chunk_size: int, output_dir: Path) -> int:
    """将对话列表切分为多个 chunk 文件。

    Returns:
        chunk 文件数量
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size 必须大于 0，当前值: {chunk_size}")

    output_dir.mkdir(parents=True, exist_ok=True)
    chunk_count = 0

    for i in range(0, len(dialogues), chunk_size):
        chunk = dialogues[i : i + chunk_size]
        chunk_file = output_dir / f"chunk_{chunk_count}.jsonl"

        with open(chunk_file, "w", encoding="utf-8") as f:
            for dialogue in chunk:
                f.write(json.dumps(dialogue, ensure_ascii=False) + "\n")

        chunk_count += 1

    logger.info(f"切分完成: {chunk_count} 个 chunk, 每个约 {chunk_size} 条对话")
    return chunk_count


def generate_phase1_report(
    total: int, success: int, errors: list[str],
    chunk_count: int, avg_turns: float,
) -> str:
    """生成 Phase 1 完成报告。"""
    lines = [
        "# Phase 1 数据预处理 - 完成报告",
        "",
        f"总对话数: {total}",
        f"成功解析: {success}",
        f"解析失败: {len(errors)}",
        f"生成 chunk: {chunk_count} 个",
        f"平均对话轮次: {avg_turns:.1f}",
        "",
    ]

    if errors:
        lines.append("## 错误详情")
        for err in errors:
            lines.append(f"- {err}")
        lines.append("")

    return "\n".join(lines)
