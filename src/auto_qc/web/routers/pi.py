"""问题挖掘页面路由。"""
import json
import uuid
import asyncio
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, Form
from fastapi.responses import HTMLResponse

from auto_qc.web.templates import templates

router = APIRouter()

# 运行中的任务状态（内存，单用户场景够用）
_running_tasks: dict = {}


@router.get("/", response_class=HTMLResponse)
async def pi_page(request: Request):
    return templates.TemplateResponse(
        request,
        "pi.html",
        {"request": request, "active_tab": "pi"},
    )


@router.post("/start")
async def start_pi(
    request: Request,
    file: UploadFile,
    domain: str = Form("recruitment"),
):
    """上传文件并启动问题挖掘任务。"""
    task_id = str(uuid.uuid4())[:8]
    save_dir = Path("output") / task_id
    save_dir.mkdir(parents=True, exist_ok=True)

    # 保存上传文件
    file_path = save_dir / (file.filename or "data.xlsx")
    content = await file.read()
    file_path.write_bytes(content)

    # 登记任务状态
    _running_tasks[task_id] = {"status": "running", "progress": 0}

    async def run():
        """后台运行问题挖掘，不阻塞 HTTP 响应。"""
        try:
            from auto_qc.pi.engine.pipeline import run_pipeline

            # run_pipeline 是同步的，在后台线程中运行
            result = await asyncio.to_thread(
                run_pipeline,
                data_path=str(file_path),
                output_dir=str(save_dir),
                domain=domain,
            )

            # 保存摘要供历史查询使用
            summary = {
                "data_file": file.filename or "data.xlsx",
                "domain": domain,
                "run_id": result.get("run_id", ""),
                "status": result.get("status", "completed"),
                "results": result.get("results", {}),
            }
            summary_path = save_dir / "summary.json"
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            _running_tasks[task_id]["status"] = "completed"
            _running_tasks[task_id]["progress"] = 100
            _running_tasks[task_id]["summary"] = summary
        except Exception as e:
            _running_tasks[task_id]["status"] = "failed"
            _running_tasks[task_id]["error"] = str(e)

    asyncio.create_task(run())

    return templates.TemplateResponse(
        request,
        "partials/pi_progress.html",
        {"request": request, "task_id": task_id},
    )


def _read_pi_log(save_dir: Path, max_lines: int = 100) -> list[str]:
    """读取 PI 运行日志的最新 max_lines 行。"""
    # PI 在 save_dir 下会按 run_id 创建子目录
    subdirs = sorted([d for d in save_dir.iterdir() if d.is_dir() and d.name[:4].isdigit()], reverse=True)
    if subdirs:
        log_file = subdirs[0] / "logs" / "harness.log"
        if log_file.exists():
            try:
                lines = log_file.read_text(encoding="utf-8").splitlines()
                return lines[-max_lines:]
            except Exception:
                pass
    # fallback: 直接找 logs/harness.log
    log_file = save_dir / "logs" / "harness.log"
    if not log_file.exists():
        log_file = save_dir / "run.log"
    if not log_file.exists():
        return []
    try:
        lines = log_file.read_text(encoding="utf-8").splitlines()
        return lines[-max_lines:]
    except Exception:
        return []


@router.get("/progress/{task_id}")
async def pi_progress(request: Request, task_id: str):
    """轮询问题挖掘进度。"""
    task = _running_tasks.get(task_id)
    if not task:
        return HTMLResponse("<div class='text-sm text-red-500'>任务不存在</div>")
    return templates.TemplateResponse(
        request,
        "partials/pi_progress.html",
        {"request": request, "task_id": task_id, "task": task},
    )


@router.get("/result/{task_id}")
async def pi_result(request: Request, task_id: str):
    """查看问题挖掘结果。"""
    save_dir = Path("output") / task_id
    summary_file = save_dir / "summary.json"

    if not summary_file.exists():
        return HTMLResponse("<div class='text-sm text-gray-400'>结果文件不存在</div>")

    try:
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return HTMLResponse("<div class='text-sm text-red-500'>结果文件解析失败</div>")

    return templates.TemplateResponse(
        request,
        "partials/pi_result.html",
        {"request": request, "summary": summary, "task_id": task_id},
    )


@router.get("/logs/{task_id}")
async def pi_logs(request: Request, task_id: str):
    """返回运行日志的 HTML 片段（终端风格）。"""
    save_dir = Path("output") / task_id
    lines = _read_pi_log(save_dir)
    return templates.TemplateResponse(
        request,
        "partials/pi_logs.html",
        {"request": request, "task_id": task_id, "lines": lines},
    )


@router.get("/history")
async def pi_history(request: Request):
    """查看问题挖掘历史记录。"""
    from auto_qc.web.routers.history import get_recent_pi_runs

    runs = get_recent_pi_runs(limit=10)
    return templates.TemplateResponse(
        request,
        "partials/pi_history.html",
        {"request": request, "runs": runs},
    )
