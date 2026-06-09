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
    parser.add_argument("--rules", help="规则 Markdown 文件路径（与 --rules-name 二选一或合并使用）")
    parser.add_argument("--rules-name", help="规则名称，用于缓存命名和后续引用。不传时从 --rules 文件名推断")
    parser.add_argument("--output", help="报告输出路径（默认 output/<timestamp>_<run_name>/ 目录下）")
    parser.add_argument("--work-dir", help="工作目录（默认 output/<timestamp>_<run_name>/）")
    parser.add_argument("--run-name", help="运行名称（默认从数据文件名推断）")

    args = parser.parse_args()

    if not args.rules and not args.rules_name:
        parser.error("请提供 --rules（规则文件）或 --rules-name（使用缓存的规则）")

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = args.run_name or Path(args.data).stem
    work_dir = args.work_dir or f"output/{timestamp}_{run_name}"
    output_path = args.output or f"{work_dir}/质检报告_{timestamp}.xlsx"

    from auto_qc.framework.orchestrator import run_qc
    asyncio.run(run_qc(
        data_path=args.data,
        rules_path=args.rules,
        rules_name=args.rules_name,
        output_path=output_path,
        work_dir=work_dir,
    ))


if __name__ == "__main__":
    main()
