"""配置页面路由。"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from auto_qc.web.templates import templates
from auto_qc.core.config import load_env_config, save_env_config, mask_api_key

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def config_page(request: Request):
    cfg = load_env_config()
    cfg["LLM_API_KEY"] = mask_api_key(cfg["LLM_API_KEY"])
    return templates.TemplateResponse(
        request,
        "config.html",
        {
            "request": request,
            "active_tab": "config",
            "config": cfg,
            "saved": request.query_params.get("saved") == "1",
        },
    )


@router.post("/save-llm")
async def save_llm_config(request: Request):
    form = await request.form()
    api_key = form.get("api_key", "")
    # 如果提交的是掩码后的 key（以 **** 开头），保留现有值不覆盖
    if api_key.startswith("****"):
        existing = load_env_config()
        api_key = existing.get("LLM_API_KEY", api_key)
    save_env_config(
        base_url=form.get("base_url", ""),
        api_key=api_key,
        model=form.get("model", ""),
    )
    return RedirectResponse(url="/config?saved=1", status_code=303)
