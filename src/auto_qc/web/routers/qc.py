"""质检页面路由。"""
import io
import json
import uuid
import asyncio
import sys
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, Form
from fastapi.responses import HTMLResponse

from auto_qc.web.templates import templates

router = APIRouter()

# 运行中的任务状态（内存，单用户场景够用）
_running_tasks: dict = {}


def _capture_output(task_id: str, save_dir: Path):
    """返回一个文件句柄和 context，将任务期间的 print 输出同时写入日志文件和终端。"""
    log_file = save_dir / "run.log"
    fh = open(log_file, "w", encoding="utf-8")

    class Tee:
        def write(self, text):
            if text.strip():
                fh.write(text)
                fh.flush()
                sys.__stdout__.write(text)
                sys.__stdout__.flush()

        def flush(self):
            fh.flush()
            sys.__stdout__.flush()

    return fh, Tee()


def _read_log(save_dir: Path, max_lines: int = 100) -> list[str]:
    """读取运行日志的最新 max_lines 行。"""
    log_file = save_dir / "run.log"
    if not log_file.exists():
        return []
    try:
        lines = log_file.read_text(encoding="utf-8").splitlines()
        return lines[-max_lines:]
    except Exception:
        return []


@router.get("/", response_class=HTMLResponse)
async def qc_page(request: Request):
    return templates.TemplateResponse(
        request,
        "qc.html",
        {"request": request, "active_tab": "qc"},
    )


@router.post("/start")
async def start_qc(
    request: Request,
    file: UploadFile,
    rule_sets: str = Form("pi-rules"),
):
    """上传文件并启动质检任务。"""
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
        """后台运行质检，不阻塞 HTTP 响应。"""
        fh, tee = _capture_output(task_id, save_dir)
        old_stdout = sys.stdout
        sys.stdout = tee  # type: ignore
        try:
            from auto_qc.core.config import load_env_config

            load_env_config()

            from auto_qc.qc.framework.orchestrator import run_qc

            await run_qc(
                data_path=str(file_path),
                rule_set_names=[s.strip() for s in rule_sets.split(",") if s.strip()],
                output_path=str(save_dir / "report.xlsx"),
                work_dir=str(save_dir),
            )
            _running_tasks[task_id]["status"] = "completed"
            _running_tasks[task_id]["progress"] = 100
        except Exception as e:
            _running_tasks[task_id]["status"] = "failed"
            _running_tasks[task_id]["error"] = str(e)
            print(f"错误: {e}")
        finally:
            sys.stdout = old_stdout
            fh.close()

    asyncio.create_task(run())

    return templates.TemplateResponse(
        request,
        "partials/qc_progress.html",
        {"request": request, "task_id": task_id},
    )


@router.get("/progress/{task_id}")
async def qc_progress(request: Request, task_id: str):
    """轮询质检进度。"""
    task = _running_tasks.get(task_id)
    if not task:
        return HTMLResponse("<div class='text-sm text-red-500'>任务不存在</div>")
    return templates.TemplateResponse(
        request,
        "partials/qc_progress.html",
        {"request": request, "task_id": task_id, "task": task},
    )


@router.get("/result/{task_id}")
async def qc_result(request: Request, task_id: str):
    """查看质检结果。"""
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
        "partials/qc_result.html",
        {"request": request, "summary": summary, "task_id": task_id},
    )


@router.get("/history")
async def qc_history(request: Request):
    """查看质检历史记录。"""
    from auto_qc.web.routers.history import get_recent_qc_runs

    runs = get_recent_qc_runs(limit=10)
    return templates.TemplateResponse(
        request,
        "partials/qc_history.html",
        {"request": request, "runs": runs},
    )


@router.get("/logs/{task_id}")
async def qc_logs(request: Request, task_id: str):
    """返回运行日志的 HTML 片段（终端风格）。"""
    save_dir = Path("output") / task_id
    lines = _read_log(save_dir)
    return templates.TemplateResponse(
        request,
        "partials/qc_logs.html",
        {"request": request, "task_id": task_id, "lines": lines},
    )


@router.get("/download/{task_id}")
async def qc_download(task_id: str):
    """下载质检报告。"""
    from fastapi.responses import FileResponse

    report_path = Path("output") / task_id / "report.xlsx"
    if not report_path.exists():
        return HTMLResponse("<div class='text-sm text-red-500'>报告文件不存在</div>")
    return FileResponse(
        str(report_path),
        filename="report.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
