"""FastAPI 应用入口。"""
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from auto_qc.web.routers import qc, pi, config


def create_app() -> FastAPI:
    app = FastAPI(title="Auto-QC-Tool", version="1.0.0")

    static_dir = Path(__file__).resolve().parent / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(qc.router, prefix="/qc", tags=["qc"])
    app.include_router(pi.router, prefix="/pi", tags=["pi"])
    app.include_router(config.router, prefix="/config", tags=["config"])

    @app.get("/")
    async def root():
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/qc/")

    return app
