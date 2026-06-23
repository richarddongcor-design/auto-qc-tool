"""统一 CLI 入口 — 子命令: qc, pi, web, config"""
import argparse
import asyncio
import datetime
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Auto-QC — 外呼通话质量检测 + 问题挖掘平台",
        prog="auto-qc",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- qc 子命令 ---
    qc_parser = subparsers.add_parser("qc", help="质检任务")
    qc_sub = qc_parser.add_subparsers(dest="qc_action", required=True)

    qc_run = qc_sub.add_parser("run", help="运行质检")
    qc_run.add_argument("--data", required=True, help="源数据 Excel 文件路径")
    qc_run.add_argument("--rule-sets", required=True, help="规则集名称，多个用逗号分隔")
    qc_run.add_argument("--output", help="报告输出路径")
    qc_run.add_argument("--work-dir", help="工作目录")

    qc_history = qc_sub.add_parser("history", help="查看质检历史记录")
    qc_history.add_argument("--limit", type=int, default=10, help="显示条数")
    qc_history.add_argument("--delete", help="删除指定 ID 的历史记录")

    qc_dl = qc_sub.add_parser("download", help="下载质检报告")
    qc_dl.add_argument("id", help="运行 ID（目录名）")
    qc_dl.add_argument("--output", "-o", help="保存路径（默认当前目录）")

    # --- pi 子命令 ---
    pi_parser = subparsers.add_parser("pi", help="问题挖掘任务")
    pi_sub = pi_parser.add_subparsers(dest="pi_action", required=True)

    pi_run = pi_sub.add_parser("run", help="运行问题挖掘")
    pi_run.add_argument("--data", required=True, help="源数据 Excel 文件路径")
    pi_run.add_argument("--domain", default="recruitment", help="领域名称")
    pi_run.add_argument("--output", help="输出目录")

    pi_history = pi_sub.add_parser("history", help="查看问题挖掘历史记录")
    pi_history.add_argument("--limit", type=int, default=10, help="显示条数")
    pi_history.add_argument("--delete", help="删除指定 ID 的历史记录")

    pi_dl = pi_sub.add_parser("download", help="下载问题挖掘结果")
    pi_dl.add_argument("id", help="运行 ID（目录名）")
    pi_dl.add_argument("--output", "-o", help="保存路径（默认当前目录）")

    # --- config 子命令 ---
    cfg_parser = subparsers.add_parser("config", help="查看/修改 LLM 配置")
    cfg_sub = cfg_parser.add_subparsers(dest="config_action", required=True)

    cfg_show = cfg_sub.add_parser("show", help="查看当前配置")
    cfg_set = cfg_sub.add_parser("set", help="修改配置")
    cfg_set.add_argument("--base-url", help="API 接口地址")
    cfg_set.add_argument("--api-key", help="API Key")
    cfg_set.add_argument("--model", help="模型名称")

    # --- web 子命令 ---
    web_parser = subparsers.add_parser("web", help="启动 Web 服务")
    web_parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    web_parser.add_argument("--port", type=int, default=8000, help="监听端口")

    args = parser.parse_args()

    if args.command == "qc":
        _handle_qc(args)
    elif args.command == "pi":
        _handle_pi(args)
    elif args.command == "web":
        _run_web(args)
    elif args.command == "config":
        _handle_config(args)


# ── QC ──────────────────────────────────────────────────────────────────────

def _handle_qc(args):
    if args.qc_action == "run":
        _run_qc(args)
    elif args.qc_action == "history":
        _qc_history(args)
    elif args.qc_action == "download":
        _qc_download(args)


def _run_qc(args):
    from auto_qc.core.config import load_env_config
    load_env_config()
    try:
        from auto_qc.qc.framework.orchestrator import run_qc
    except ImportError as e:
        print(f"错误: 无法导入质检模块 — {e}")
        sys.exit(1)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = Path(args.output).stem if args.output else Path(args.data).stem
    work_dir = args.work_dir or f"output/{timestamp}_{run_name}"
    output_path = args.output or f"{work_dir}/质检报告_{timestamp}.xlsx"
    rule_set_names = [s.strip() for s in args.rule_sets.split(",") if s.strip()]
    if not rule_set_names:
        print("错误: --rule-sets 至少需要指定一个规则集")
        sys.exit(1)
    try:
        asyncio.run(run_qc(data_path=args.data, rule_set_names=rule_set_names,
                           output_path=output_path, work_dir=work_dir))
        print(f"质检完成，报告已保存至: {output_path}")
    except Exception as e:
        print(f"错误: 质检运行失败 — {e}")
        sys.exit(1)


def _qc_history(args):
    if args.delete:
        import shutil
        path = Path("output") / args.delete
        if path.exists():
            shutil.rmtree(path)
            print(f"已删除记录: {args.delete}")
        else:
            print(f"记录不存在: {args.delete}")
        return

    from auto_qc.web.routers.history import get_recent_qc_runs
    runs = get_recent_qc_runs(limit=args.limit)
    if not runs:
        print("暂无历史记录")
        return
    print(f"{'ID':<12} {'文件':<30} {'对话':<6} {'违规率':<8} {'状态':<6}")
    print("-" * 70)
    for r in runs:
        print(f"{r['id']:<12} {r['data_file'].split('/')[-1]:<30} {r['total']:<6} {r['violation_rate']:<8} {'失败' if r.get('status') == 'failed' else '成功'}")


def _qc_download(args):
    report_path = Path("output") / args.id / "report.xlsx"
    if not report_path.exists():
        print(f"错误: 报告不存在 ({report_path})")
        sys.exit(1)
    import shutil
    dest = Path(args.output or ".") / f"质检报告_{args.id}.xlsx"
    shutil.copy2(str(report_path), str(dest))
    print(f"已下载: {dest}")


# ── PI ──────────────────────────────────────────────────────────────────────

def _handle_pi(args):
    if args.pi_action == "run":
        _run_pi(args)
    elif args.pi_action == "history":
        _pi_history(args)
    elif args.pi_action == "download":
        _pi_download(args)


def _run_pi(args):
    from auto_qc.core.config import load_env_config
    load_env_config()
    try:
        from auto_qc.pi.engine.pipeline import run_pipeline
    except ImportError as e:
        print(f"错误: 无法导入问题挖掘模块 — {e}")
        sys.exit(1)
    try:
        result = run_pipeline(data_path=args.data, output_dir=args.output, domain=args.domain)
        status = result.get("status", "unknown")
        print(f"问题挖掘完成，状态: {status}")
    except Exception as e:
        print(f"错误: 问题挖掘运行失败 — {e}")
        sys.exit(1)


def _pi_history(args):
    if args.delete:
        import shutil
        path = Path("output") / args.delete
        if path.exists():
            shutil.rmtree(path)
            print(f"已删除记录: {args.delete}")
        else:
            print(f"记录不存在: {args.delete}")
        return

    from auto_qc.web.routers.history import get_recent_pi_runs
    runs = get_recent_pi_runs(limit=args.limit)
    if not runs:
        print("暂无历史记录")
        return
    print(f"{'ID':<12} {'文件':<30} {'领域':<10} {'状态':<6}")
    print("-" * 60)
    for r in runs:
        status = "失败" if r.get("status") == "failed" else "成功"
        print(f"{r['id']:<12} {r['data_file'].split('/')[-1]:<30} {r.get('domain',''):<10} {status:<6}")


def _pi_download(args):
    save_dir = Path("output") / args.id
    if not save_dir.exists():
        print(f"错误: 运行目录不存在 ({save_dir})")
        sys.exit(1)

    # 找报告文件
    subdirs = sorted([d for d in save_dir.iterdir() if d.is_dir() and d.name[:4].isdigit()], reverse=True)
    found = None
    for sd in subdirs:
        for fname in ["rules_summary.md", "rules.md", "report.md"]:
            fp = sd / fname
            if fp.exists():
                found = fp
                break
        if found:
            break

    if found:
        dest = Path(args.output or ".") / f"挖掘报告_{args.id}.md"
        import shutil
        shutil.copy2(str(found), str(dest))
        print(f"已下载: {dest}")
    else:
        # 整个目录打包下载
        import tarfile, io, shutil
        dest = Path(args.output or ".") / f"挖掘结果_{args.id}"
        dest.mkdir(parents=True, exist_ok=True)
        for item in save_dir.rglob("*"):
            if item.is_file():
                rel = item.relative_to(save_dir)
                (dest / rel.parent).mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(item), str(dest / rel))
        print(f"已下载至目录: {dest}")


# ── Config ──────────────────────────────────────────────────────────────────

def _handle_config(args):
    from auto_qc.core.config import load_env_config, save_env_config, mask_api_key

    if args.config_action == "show":
        cfg = load_env_config()
        print(f"接口地址: {cfg['LLM_BASE_URL']}")
        print(f"API Key:   {mask_api_key(cfg['LLM_API_KEY'])}")
        print(f"模型:      {cfg['LLM_MODEL']}")

    elif args.config_action == "set":
        cfg = load_env_config()
        base_url = args.base_url or cfg["LLM_BASE_URL"]
        api_key = args.api_key or cfg["LLM_API_KEY"]
        model = args.model or cfg["LLM_MODEL"]
        save_env_config(base_url=base_url, api_key=api_key, model=model)
        print("配置已更新")

# ── Web ─────────────────────────────────────────────────────────────────────

def _run_web(args):
    from auto_qc.core.config import load_env_config
    load_env_config()
    try:
        import uvicorn
    except ImportError:
        print("错误: 启动 Web 服务需要安装 uvicorn")
        sys.exit(1)
    try:
        from auto_qc.web.app import create_app
    except ImportError as e:
        print(f"错误: 无法导入 Web 模块 — {e}")
        sys.exit(1)
    app = create_app()
    print(f"启动 Web 服务: http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
