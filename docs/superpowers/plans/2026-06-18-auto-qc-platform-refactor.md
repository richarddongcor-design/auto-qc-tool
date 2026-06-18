# Auto-QC 平台重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 auto-qc（质检）和 auto-pi（问题挖掘）融合为统一平台，提供 Web UI + CLI 双模式入口

**Architecture:** FastAPI + Jinja2 + HTMX 前端，`core/` 共享 LLM 客户端和配置管理，`qc/` 和 `pi/` 独立业务模块，统一 CLI 子命令分发

**Tech Stack:** Python 3.13+, FastAPI, Jinja2, HTMX (CDN), Tailwind CSS (CDN), openpyxl, httpx[socks], python-dotenv

## Global Constraints

- Python >= 3.13
- LLM 配置全局共享（core/llm.py），QC 和 PI 通过同一套配置调用 LLM
- 无数据库依赖，所有状态存文件系统
- API Key 在 .env 中存储，页面仅显示掩码（末尾 4 位明文）
- 保留现有 rules/ JSON 格式不变
- 现有 `output/` 目录结构和报告格式不变

---

### Task 1: 创建核心公共组件（core/llm.py + core/config.py）

**Files:**
- Create: `src/auto_qc/core/__init__.py`
- Create: `src/auto_qc/core/llm.py` — 统一 LLM 客户端
- Create: `src/auto_qc/core/config.py` — 配置读写管理

**Interfaces:**
- Produces: `call_llm(prompt: str, max_tokens: int = 8000) -> str`
- Produces: `call_llm_with_retry(prompt: str, max_tokens: int = 8000) -> str`
- Produces: `get_token_stats() -> TokenStats`
- Produces: `reset_token_stats()`
- Produces: `load_env_config() -> dict`（返回 {base_url, api_key, model}）
- Produces: `save_env_config(base_url, api_key, model) -> None`

- [ ] **Step 1: 创建 `core/__init__.py`**

```python
# 空文件
```

- [ ] **Step 2: 创建 `core/llm.py`**

从当前 `framework/worker.py` 移植，关键改动：
- 去掉 `load_dotenv()` — 由调用方控制
- 对外暴露 `call_llm()`、`call_llm_with_retry()`、`get_token_stats()`、`reset_token_stats()`
- 函数签名与现有 `worker.py` 保持一致，方便下游迁移

```python
"""统一 LLM 调用封装 — QC 和 PI 共享。"""
import os
import json
import asyncio
import re
import httpx
from dataclasses import dataclass
from openai import AsyncOpenAI
from json_repair import repair_json

MAX_RETRIES = 3


@dataclass
class TokenStats:
    total_input: int = 0
    total_output: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.total_input += input_tokens
        self.total_output += output_tokens

    @property
    def total(self) -> int:
        return self.total_input + self.total_output

    def summary(self) -> dict:
        return {
            "total_input_tokens": self.total_input,
            "total_output_tokens": self.total_output,
            "total_tokens": self.total,
        }


_token_stats = TokenStats()


def get_token_stats() -> TokenStats:
    return _token_stats


def reset_token_stats() -> None:
    _token_stats.total_input = 0
    _token_stats.total_output = 0


def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
        api_key=os.environ.get("LLM_API_KEY", ""),
        timeout=httpx.Timeout(120.0, connect=30.0),
    )


def _get_model() -> str:
    return os.environ.get("LLM_MODEL", "deepseek-chat")


async def call_llm(prompt: str, max_tokens: int = 8000) -> str:
    client = _get_client()
    model = _get_model()
    response = await client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    usage = response.usage
    if usage:
        _token_stats.add(
            input_tokens=usage.prompt_tokens or 0,
            output_tokens=usage.completion_tokens or 0,
        )
    return response.choices[0].message.content or ""


async def call_llm_with_retry(prompt: str, max_tokens: int = 8000) -> str:
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            return await call_llm(prompt, max_tokens)
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
    raise RuntimeError(f"LLM 调用失败（重试 {MAX_RETRIES} 次后）: {last_error}")


def extract_json(text: str) -> str:
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    try:
        repaired = repair_json(text)
        json.loads(repaired)
        return repaired
    except (json.JSONDecodeError, Exception):
        pass
    match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if match:
        inner = match.group(1).strip()
        try:
            repair_json(inner)
            return inner
        except Exception:
            pass
    raise ValueError("无法从 LLM 响应中提取有效 JSON")
```

- [ ] **Step 3: 创建 `core/config.py`**

功能：读取和写入 `.env` 文件（位于项目根目录）。写入时不破坏已有注释和其他变量。

```python
"""全局配置管理 — LLM 配置读写。"""
import os
import re
from pathlib import Path


def _find_env_path() -> Path:
    """从项目根目录查找 .env 文件。"""
    return Path(__file__).resolve().parent.parent.parent.parent / ".env"


def load_env_config() -> dict:
    """读取当前 .env 配置。"""
    env_path = _find_env_path()
    config = {
        "LLM_BASE_URL": "https://api.deepseek.com",
        "LLM_API_KEY": "",
        "LLM_MODEL": "deepseek-chat",
    }
    if not env_path.exists():
        return config
    content = env_path.read_text(encoding="utf-8")
    for key in config:
        m = re.search(rf"^{re.escape(key)}=(.+)$", content, re.MULTILINE)
        if m:
            config[key] = m.group(1).strip()
    return config


def mask_api_key(key: str) -> str:
    """掩码 API Key，仅显示末 4 位。"""
    if len(key) <= 8:
        return "****"
    return "****" + key[-4:]


def save_env_config(base_url: str, api_key: str, model: str) -> None:
    """保存 LLM 配置到 .env 文件。"""
    env_path = _find_env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)

    keys = ["LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"]
    values = [base_url, api_key, model]

    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
    else:
        content = "# auto-qc API 配置\n"

    for key, val in zip(keys, values):
        if re.search(rf"^{re.escape(key)}=", content, re.MULTILINE):
            content = re.sub(
                rf"^{re.escape(key)}=.*$",
                f"{key}={val}",
                content,
                flags=re.MULTILINE,
            )
        else:
            content += f"{key}={val}\n"

    env_path.write_text(content, encoding="utf-8")

    # 立即更新当前进程环境变量
    os.environ["LLM_BASE_URL"] = base_url
    os.environ["LLM_API_KEY"] = api_key
    os.environ["LLM_MODEL"] = model
```

- [ ] **Step 4: 验证脚本**

```bash
uv run python -c "
import asyncio
from auto_qc.core.llm import call_llm, get_token_stats, reset_token_stats
reset_token_stats()
text = asyncio.run(call_llm('Return just OK'))
print('Response:', text)
print('Tokens:', get_token_stats().summary())
"
```

预期输出：API 返回 OK，Token 统计非零。

- [ ] **Step 5: Commit**

```bash
git add src/auto_qc/core/
git commit -m "feat: 创建 core/ 公共组件（LLM 客户端 + 配置管理）"
```

---

### Task 2: 重构现有 QC 代码到 qc/ 子包

**Files:**
- Create: `src/auto_qc/qc/__init__.py`
- Move: `src/auto_qc/domain/*` → `src/auto_qc/qc/domain/*`
- Move: `src/auto_qc/framework/*` → `src/auto_qc/qc/framework/*`
- Modify: `src/auto_qc/qc/framework/worker.py` — 改为调用 `core/llm.py`
- Modify: `src/auto_qc/qc/framework/orchestrator.py` — 更新 import 路径

**Interfaces:**
- Consumes: `from auto_qc.core.llm import call_llm_with_retry, extract_json, ...`
- Produces: `run_qc(data_path, rule_set_names, output_path, work_dir)` — 签名不变

- [ ] **Step 1: 创建目录并移动文件**

```bash
mkdir -p src/auto_qc/qc/domain src/auto_qc/qc/framework
# 移动 domain 文件
git mv src/auto_qc/domain/__init__.py src/auto_qc/qc/domain/
git mv src/auto_qc/domain/*.py src/auto_qc/qc/domain/
# 移动 framework 文件
git mv src/auto_qc/framework/__init__.py src/auto_qc/qc/framework/
git mv src/auto_qc/framework/*.py src/auto_qc/qc/framework/
```

- [ ] **Step 2: 修改 `qc/framework/worker.py`**

将开头的 `from openai import AsyncOpenAI` ... 直到 `extract_json` 的全部 LLM 调用代码替换为：

```python
"""QC 特有的 worker 逻辑（LLM 调用委托给 core/llm.py）。"""
from auto_qc.core.llm import call_llm_with_retry, extract_json, get_token_stats, reset_token_stats
```

保留文件中原有的 `MAX_RETRIES` 等常量（目前 worker.py 里这些已在 core/llm.py 中，所以清理掉重复定义，只保留 import）。

- [ ] **Step 3: 更新 `qc/framework/orchestrator.py` 的 import 路径**

```python
# 原有
from auto_qc.domain.rules import load_rule_sets, validate_rule_sets
from auto_qc.domain.data_loader import load_conversations, save_batches
# ...

# 改为
from auto_qc.qc.domain.rules import load_rule_sets, validate_rule_sets
from auto_qc.qc.domain.data_loader import load_conversations, save_batches
# ... 同理修改所有 domain 和 framework 的内部引用
```

需要改的文件：
- `qc/framework/orchestrator.py` — 所有 `auto_qc.domain.*` → `auto_qc.qc.domain.*`, `auto_qc.framework.*` → `auto_qc.qc.framework.*`
- `qc/framework/validator.py` — 引用 `domain.schemas`
- `qc/framework/coordinator.py` — 引用 `domain.schemas`
- `qc/framework/cross_validator.py` — 引用可能涉及 domain
- `qc/domain/prompts.py` — 引用 `domain.schemas` → `qc.domain.schemas`
- `qc/domain/merger.py` — 引用 `domain.schemas`
- `qc/domain/report.py` — 引用 `domain.schemas`

关键：所有 `auto_qc.domain` 改为 `auto_qc.qc.domain`，所有 `auto_qc.framework` 改为 `auto_qc.qc.framework`。

- [ ] **Step 4: 确保内部跨模块引用正确**

`qc/domain/*.py` 之间相互引用时（比如 `rules.py` 引用 `schemas.py`），原本是：
```python
from auto_qc.domain.schemas import Rule, RuleSet
```
改为：
```python
from auto_qc.qc.domain.schemas import Rule, RuleSet
```

- [ ] **Step 5: 验证 QC 模块可导入**

```bash
uv run python -c "
from auto_qc.qc.domain.rules import load_rule_sets, validate_rule_sets
rs = load_rule_sets(['pi-rules'])
print(f'Loaded {len(rs)} rule set(s), {len(rs[0].rules)} rules')
errors = validate_rule_sets(rs)
print(f'Validation errors: {errors}')
"
```

预期输出：成功加载，无错误。

- [ ] **Step 6: 删除空目录**

```bash
# 原 domain 和 framework 目录已空，删除
rmdir src/auto_qc/domain 2>/dev/null; rmdir src/auto_qc/framework 2>/dev/null
```

- [ ] **Step 7: Commit**

```bash
git add src/auto_qc/qc/ src/auto_qc/core/
git rm -r src/auto_qc/domain/ src/auto_qc/framework/ 2>/dev/null; true
git commit -m "refactor: 将 QC 代码重构到 qc/ 子包，LLM 调用委托给 core/"
```

---

### Task 3: 合并 PI（问题挖掘）代码

**Files:**
- Create: `src/auto_qc/pi/` — 从 auto-pi 项目复制
- Modify: `src/auto_qc/pi/engine/scheduler.py` — 改用 `core/llm.py`
- Modify: `src/auto_qc/pi/engine/pipeline.py` — main() 改为可调用的函数

**Interfaces:**
- Produces: `run_pipeline(data_path, output_dir, domain, config_path) -> dict`

- [ ] **Step 1: 复制 auto-pi 代码到 pi/**

```bash
cp -r /c/Users/dongyi/myprojects/auto-pi/harness/* src/auto_qc/pi/
```

- [ ] **Step 2: 创建 pi/__init__.py**

```python
"""问题挖掘业务模块。"""
```

- [ ] **Step 3: 修改包内 import 路径**

PI 的内部 import 都是 `from harness.xxx` 形式，需要批量改为 `from auto_qc.pi.xxx`：

```bash
# 将所有文件中 from harness. 替换为 from auto_qc.pi.
# 手动遍历并修改
```

涉及文件：
- `pi/engine/pipeline.py` — `from harness.agents.config` → `from auto_qc.pi.agents.config`
- `pi/engine/scheduler.py` — `from harness.agents.config` → `from auto_qc.pi.agents.config`, `from harness.engine.validator` → `from auto_qc.pi.engine.validator`
- `pi/engine/tracer.py`
- `pi/engine/validator.py`
- `pi/agents/__init__.py`
- `pi/core/domain_loader.py` — `from harness.domains`
- `pi/utils/excel_parser.py`
- `pi/utils/report_generator.py`

- [ ] **Step 4: 修改 `pi/engine/scheduler.py` 改用 `core/llm.py`**

将原有的 OpenAI 客户端初始化替换为共用配置。PI 的 scheduler 使用同步 OpenAI 客户端（`from openai import OpenAI`），而 `core/llm.py` 使用异步客户端（`AsyncOpenAI`）。

由于 PI 的管线是同步的（`ThreadPoolExecutor`），最简方案是在 scheduler 中直接读取 `.env` 配置创建同步客户端，但配置来源统一用 `core/config.py` 管理的值。

实际上更简单的方式：让 scheduler 仍然用自己的客户端创建方式，但配置从环境变量读（与 core/llm.py 同一套来源）。因为核心配置都来自于 `.env`，所以自然就共享了。

修改 `scheduler.py`：
- 删除 `LlmConfig` 中从 `.env` 读取的 fallback 逻辑（或者保留，反正来源相同）
- 确保使用 `os.environ.get("LLM_BASE_URL")` 等，而不是从 `config.yaml` 读

- [ ] **Step 5: 将 `pipeline.main()` 改为可调用函数**

```python
# 在 pipeline.py 末尾新增
def run_pipeline(
    data_path: str,
    output_dir: str | None = None,
    domain: str = "recruitment",
) -> dict:
    """供 CLI 和 Web UI 调用的统一入口。"""
    # ... 复用 main() 中的核心逻辑 ...
```

保持原有 `main()` CLI 入口不变。

- [ ] **Step 6: 验证 PI 模块可导入**

```bash
uv run python -c "
from auto_qc.pi.agents.config import HarnessConfig
print('PI module OK')
"
```

预期：无 ImportError。

- [ ] **Step 7: Commit**

```bash
git add src/auto_qc/pi/
git commit -m "feat: 合并 PI（问题挖掘）代码到 pi/ 子包"
```

---

### Task 4: 统一 CLI 入口

**Files:**
- Modify: `src/auto_qc/cli.py` — 添加子命令 qc 和 pi

- [ ] **Step 1: 重写 `cli.py`**

```python
"""统一 CLI 入口 — 子命令: qc, pi"""
import argparse
import asyncio
import datetime
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
    qc_run.add_argument("--rule-sets", required=True,
                        help="规则集名称，多个用逗号分隔")
    qc_run.add_argument("--output", help="报告输出路径")
    qc_run.add_argument("--work-dir", help="工作目录")

    # --- pi 子命令 ---
    pi_parser = subparsers.add_parser("pi", help="问题挖掘任务")
    pi_sub = pi_parser.add_subparsers(dest="pi_action", required=True)

    pi_run = pi_sub.add_parser("run", help="运行问题挖掘")
    pi_run.add_argument("--data", required=True, help="源数据 Excel 文件路径")
    pi_run.add_argument("--domain", default="recruitment", help="领域名称")
    pi_run.add_argument("--output", help="输出目录")

    args = parser.parse_args()

    if args.command == "qc":
        _run_qc(args)
    elif args.command == "pi":
        _run_pi(args)


def _run_qc(args):
    from auto_qc.core.config import load_env_config
    load_env_config()  # 确保配置加载

    from auto_qc.qc.framework.orchestrator import run_qc
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = args.output and Path(args.output).stem or Path(args.data).stem
    work_dir = args.work_dir or f"output/{timestamp}_{run_name}"
    output_path = args.output or f"{work_dir}/质检报告_{timestamp}.xlsx"

    rule_set_names = [s.strip() for s in args.rule_sets.split(",") if s.strip()]
    if not rule_set_names:
        print("错误: --rule-sets 至少需要指定一个规则集")
        sys.exit(1)

    asyncio.run(run_qc(
        data_path=args.data,
        rule_set_names=rule_set_names,
        output_path=output_path,
        work_dir=work_dir,
    ))


def _run_pi(args):
    from auto_qc.core.config import load_env_config
    load_env_config()

    from auto_qc.pi.engine.pipeline import run_pipeline
    run_pipeline(data_path=args.data, output_dir=args.output, domain=args.domain)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 测试 CLI**

```bash
uv run auto-qc --help
# 应显示 qc 和 pi 子命令

uv run auto-qc qc run --help
# 应显示 --data, --rule-sets 等参数

uv run auto-qc pi run --help
# 应显示 --data, --domain, --output 等参数
```

- [ ] **Step 3: Commit**

```bash
git add src/auto_qc/cli.py
git commit -m "feat: 统一 CLI 入口，添加 qc/pi 子命令"
```

---

### Task 5: 添加 FastAPI 依赖 + 更新 pyproject.toml

**Files:**
- Modify: `pyproject.toml` — 添加 fastapi, uvicorn, python-multipart

- [ ] **Step 1: 添加 Web 依赖**

```bash
uv add "fastapi>=0.115.0" "uvicorn[standard]>=0.34.0" python-multipart
```

- [ ] **Step 2: 验证依赖安装**

```bash
uv run python -c "import fastapi; import uvicorn; print('Dependencies OK')"
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: 添加 FastAPI + Uvicorn + python-multipart 依赖"
```

---

### Task 6: Web 应用骨架 + 静态资源

**Files:**
- Create: `src/auto_qc/web/__init__.py`
- Create: `src/auto_qc/web/app.py` — FastAPI 应用工厂
- Create: `src/auto_qc/web/routers/__init__.py`
- Create: `src/auto_qc/web/templates/base.html` — 基础布局模板
- Create: `src/auto_qc/web/templates/qc.html` — 质检页面
- Create: `src/auto_qc/web/templates/pi.html` — 问题挖掘页面
- Create: `src/auto_qc/web/templates/config.html` — 配置页面

**Interfaces:**
- Produces: `create_app() -> FastAPI` — 应用工厂
- Produces: Web routes `/qc`, `/pi`, `/config`

- [ ] **Step 1: 创建目录结构**

```bash
mkdir -p src/auto_qc/web/routers src/auto_qc/web/templates src/auto_qc/web/static
```

- [ ] **Step 2: 创建 `web/__init__.py`**

```python
"""Web UI 模块。"""
```

- [ ] **Step 3: 创建基础模板 `templates/base.html`**

使用 Tailwind CSS (CDN) + 简洁布局。参考原型设计：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{% block title %}Auto-QC{% endblock %}</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/htmx.org@2.0.4"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
  * { font-family: 'Inter', -apple-system, 'PingFang SC', sans-serif; }
  body { background: #fafafa; }
  .nav-item { @apply text-sm text-gray-400 hover:text-gray-900 transition-colors px-1 py-0.5; }
  .nav-item.active { @apply text-gray-900 font-medium; }
  .card { @apply bg-white rounded-xl border border-gray-100 shadow-sm; }
  .btn-primary { @apply px-5 py-2 bg-gray-900 hover:bg-gray-800 text-white text-sm font-medium rounded-lg transition-all cursor-pointer; }
  .btn-secondary { @apply px-5 py-2 border border-gray-200 hover:border-gray-300 text-gray-600 text-sm font-medium rounded-lg transition-all cursor-pointer; }
  input, select, textarea { @apply w-full px-3.5 py-2.5 bg-gray-50 border border-gray-200 rounded-lg text-sm outline-none focus:border-gray-400 focus:bg-white transition-all; }
  label { @apply text-xs font-medium text-gray-500 mb-1.5 block; }
</style>
</head>
<body>
<div class="max-w-5xl mx-auto px-8 py-8">
  <!-- Header -->
  <div class="flex items-center justify-between mb-10">
    <div class="flex items-center gap-8">
      <a href="/" class="flex items-center gap-2.5 no-underline">
        <div class="w-7 h-7 rounded-lg bg-gray-900 flex items-center justify-center text-white text-xs font-bold">Q</div>
        <span class="font-semibold text-sm text-gray-900">Auto-QC</span>
      </a>
      <nav class="flex items-center gap-6">
        <a href="/qc" class="nav-item {% if active_tab == 'qc' %}active{% endif %}">质检</a>
        <a href="/pi" class="nav-item {% if active_tab == 'pi' %}active{% endif %}">问题挖掘</a>
        <a href="/config" class="nav-item {% if active_tab == 'config' %}active{% endif %}">配置</a>
      </nav>
    </div>
    <div id="llm-status" class="flex items-center gap-2 text-xs text-gray-400">
      <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
      <span id="model-name">就绪</span>
    </div>
  </div>
  {% block content %}{% endblock %}
</div>
</body>
</html>
```

- [ ] **Step 4: 创建 `web/app.py`**

```python
"""FastAPI 应用入口。"""
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from auto_qc.web.routers import qc, pi, config

templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent / "templates"
)


def create_app() -> FastAPI:
    app = FastAPI(title="Auto-QC", version="0.2.0")

    static_dir = Path(__file__).resolve().parent / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(qc.router, prefix="/qc", tags=["qc"])
    app.include_router(pi.router, prefix="/pi", tags=["pi"])
    app.include_router(config.router, prefix="/config", tags=["config"])

    @app.get("/")
    async def root():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/qc")

    return app
```

- [ ] **Step 5: 创建路由文件**

`web/routers/__init__.py` — 空

`web/routers/qc.py` — 质检页面路由，初始返回渲染模板：

```python
"""质检页面路由。"""
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from auto_qc.web.app import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def qc_page(request: Request):
    return templates.TemplateResponse(
        "qc.html",
        {"request": request, "active_tab": "qc"},
    )
```

`web/routers/pi.py` — 同上述模式，返回 pi.html

`web/routers/config.py` — 配置页面路由：

```python
"""配置页面路由。"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from auto_qc.web.app import templates
from auto_qc.core.config import load_env_config, save_env_config, mask_api_key

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def config_page(request: Request):
    config = load_env_config()
    config["LLM_API_KEY"] = mask_api_key(config["LLM_API_KEY"])
    return templates.TemplateResponse(
        "config.html",
        {"request": request, "active_tab": "config", "config": config},
    )


@router.post("/save-llm")
async def save_llm_config(request: Request):
    form = await request.form()
    save_env_config(
        base_url=form.get("base_url", ""),
        api_key=form.get("api_key", ""),
        model=form.get("model", ""),
    )
    return RedirectResponse(url="/config", status_code=303)
```

- [ ] **Step 6: 创建质检页面模板 `templates/qc.html`**

```html
{% extends "base.html" %}
{% block title %}质检 — Auto-QC{% endblock %}
{% block content %}
<!-- 上传+配置区 -->
<div class="card p-6 mb-4">
  <form hx-post="/qc/start" hx-target="#result-area" hx-swap="innerHTML"
        enctype="multipart/form-data" class="space-y-5">
    <div class="flex items-start gap-6">
      <div class="flex-1">
        <label>数据文件</label>
        <div class="border-2 border-dashed border-gray-200 rounded-xl p-6 text-center hover:border-gray-300 cursor-pointer transition-colors"
             onclick="document.getElementById('file-input').click()">
          <div class="text-gray-400 text-sm mb-1">点击上传或拖拽 Excel 文件</div>
          <div class="text-xs text-gray-300">.xlsx</div>
        </div>
        <input id="file-input" type="file" name="file" accept=".xlsx" class="hidden" required>
      </div>
      <div class="flex-1 space-y-3">
        <div>
          <label>规则集</label>
          <select name="rule_sets">
            <option value="pi-rules">pi-rules（6 条规则）</option>
            <option value="intention-recruit-tree">intention-recruit-tree（4 条规则）</option>
          </select>
        </div>
        <div class="flex gap-2">
          <div class="flex-1">
            <label>模型</label>
            <select name="model">
              <option>deepseek-v4-flash</option>
              <option>deepseek-v4-pro</option>
            </select>
          </div>
          <div class="flex-1">
            <label>并发数</label>
            <input type="number" name="concurrency" value="50">
          </div>
        </div>
      </div>
    </div>
    <div class="flex justify-end pt-4 border-t border-gray-50">
      <button type="submit" class="btn-primary">开始质检</button>
    </div>
  </form>
</div>

<!-- 结果区域（HTMX 填充） -->
<div id="result-area"></div>

<!-- 历史记录 -->
<div class="card p-6">
  <div class="flex items-center justify-between mb-4">
    <span class="text-sm font-medium">历史记录</span>
  </div>
  <div id="history-list" hx-get="/qc/history" hx-trigger="load" hx-swap="innerHTML">
    <div class="text-sm text-gray-400 py-4 text-center">加载中...</div>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 7: 添加 `uvicorn` 运行入口**

在 `pyproject.toml` 中或新建模块入口。最简方式：在 `cli.py` 添加 `web` 子命令：

```python
# 在 cli.py 的 main() 中添加
web_parser = subparsers.add_parser("web", help="启动 Web UI")
web_parser.add_argument("--host", default="127.0.0.1")
web_parser.add_argument("--port", type=int, default=8000)

# 在分发逻辑中添加
elif args.command == "web":
    import uvicorn
    uvicorn.run("auto_qc.web.app:create_app", host=args.host, port=args.port, factory=True)
```

- [ ] **Step 8: 验证 Web 可启动**

```bash
# 仅测试导入和启动（运行 3 秒后停止）
timeout 3 uv run uvicorn auto_qc.web.app:create_app --factory --port 8000 2>&1 || true
```

预期：Uvicorn 启动无报错。

- [ ] **Step 9: Commit**

```bash
git add src/auto_qc/web/ src/auto_qc/cli.py
git commit -m "feat: Web UI 骨架 + 质检页面模板 + 配置页面"
```

---

---

### Task 7: Web UI 完善 — 任务提交、进度轮询、结果展示

**Files:**
- Modify: `src/auto_qc/web/routers/qc.py` — 添加任务提交和进度接口
- Modify: `src/auto_qc/web/routers/pi.py` — 同 QC
- Create: `src/auto_qc/web/routers/history.py` — 历史记录查询

**Interfaces:**
- `POST /qc/start` — 上传文件并启动质检任务，返回任务 ID
- `GET /qc/progress/{task_id}` — 返回进度 JSON（HTMX 轮询）
- `GET /qc/result/{task_id}` — 返回结果 HTML
- `GET /qc/history` — 返回历史记录 HTML 片段

- [ ] **Step 1: QC 任务提交接口**

```python
# web/routers/qc.py — 添加
import uuid
import asyncio
import json
from pathlib import Path
from fastapi import UploadFile, Form, BackgroundTasks

# 运行中的任务状态
_running_tasks: dict = {}


@router.post("/start")
async def start_qc(
    request: Request,
    file: UploadFile,
    rule_sets: str = Form("pi-rules"),
    model: str = Form("deepseek-v4-flash"),
    concurrency: int = Form(50),
):
    task_id = str(uuid.uuid4())[:8]
    save_dir = Path(f"output/{task_id}")
    save_dir.mkdir(parents=True, exist_ok=True)

    # 保存上传文件
    file_path = save_dir / file.filename
    content = await file.read()
    file_path.write_bytes(content)

    # 在后台运行质检
    _running_tasks[task_id] = {"status": "running", "progress": 0}

    async def run():
        try:
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

    asyncio.create_task(run())

    # 返回进度轮询用的 HTML 片段
    return templates.TemplateResponse(
        "partials/qc_progress.html",
        {"request": request, "task_id": task_id},
    )
```

- [ ] **Step 2: 进度轮询接口**

```python
@router.get("/progress/{task_id}")
async def qc_progress(request: Request, task_id: str):
    task = _running_tasks.get(task_id)
    if not task:
        return HTMLResponse("<div class='text-sm text-red-500'>任务不存在</div>")

    return templates.TemplateResponse(
        "partials/qc_progress.html",
        {"request": request, "task_id": task_id, "task": task},
    )
```

`templates/partials/qc_progress.html`：

```html
<div id="progress-{{ task_id }}" hx-get="/qc/progress/{{ task_id }}" hx-trigger="every 2s" hx-swap="outerHTML">
  {% if task.status == "running" %}
  <div class="card p-6 mb-4">
    <div class="flex items-center justify-between mb-4">
      <div class="flex items-center gap-3">
        <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-blue-50 text-blue-600">质检中</span>
        <span class="text-sm font-medium">{{ task_id }}</span>
      </div>
    </div>
    <div class="w-full h-1 bg-gray-100 rounded-full overflow-hidden mb-2">
      <div class="h-full bg-gray-900 rounded-full" style="width: {{ task.progress }}%"></div>
    </div>
    <div class="text-xs text-gray-400">处理中...</div>
  </div>
  {% elif task.status == "completed" %}
  <div hx-get="/qc/result/{{ task_id }}" hx-trigger="load" hx-swap="outerHTML"></div>
  {% elif task.status == "failed" %}
  <div class="card p-6 mb-4">
    <div class="text-sm text-red-500">运行失败: {{ task.error }}</div>
  </div>
  {% endif %}
</div>
```

- [ ] **Step 3: 结果展示接口**

```python
@router.get("/result/{task_id}")
async def qc_result(request: Request, task_id: str):
    save_dir = Path(f"output/{task_id}")
    summary_file = save_dir / "summary.json"
    report_file = save_dir / "report.xlsx"

    if not summary_file.exists():
        return HTMLResponse("<div class='text-sm text-gray-400'>结果文件不存在</div>")

    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    return templates.TemplateResponse(
        "partials/qc_result.html",
        {"request": request, "summary": summary, "task_id": task_id},
    )
```

`templates/partials/qc_result.html`：

```html
<div class="card p-6 mb-4">
  <div class="flex items-center justify-between mb-5">
    <div class="flex items-center gap-3">
      <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-blue-50 text-blue-600">已完成</span>
      <span class="text-sm font-medium">{{ summary.data_file.split('/')[-1] or summary.data_file }}</span>
    </div>
    <div class="flex items-center gap-3">
      <a href="/qc/download/{{ task_id }}" class="btn-secondary text-xs px-3 py-1.5 no-underline">下载报告</a>
    </div>
  </div>
  <div class="grid grid-cols-5 gap-4 mb-5">
    <div><div class="text-2xl font-semibold text-gray-900">{{ summary.total_conversations }}</div><div class="text-xs text-gray-400 mt-0.5">对话</div></div>
    <div><div class="text-2xl font-semibold text-red-500">{{ summary.violation_rate }}</div><div class="text-xs text-gray-400 mt-0.5">违规率</div></div>
    <div><div class="text-2xl font-semibold text-gray-900">{{ summary.token_usage.total_tokens / 1000 | round(1) }}K</div><div class="text-xs text-gray-400 mt-0.5">总 tokens</div></div>
  </div>
</div>
```

- [ ] **Step 4: 历史记录接口**

```python
@router.get("/history")
async def qc_history(request: Request):
    from auto_qc.web.routers.history import get_recent_qc_runs
    runs = get_recent_qc_runs(limit=10)
    return templates.TemplateResponse(
        "partials/qc_history.html",
        {"request": request, "runs": runs},
    )
```

`web/routers/history.py`：

```python
"""历史记录查询。"""
import json
from pathlib import Path


def get_recent_qc_runs(limit: int = 10) -> list[dict]:
    """扫描 output/ 目录获取最近的质检运行记录。"""
    output_dir = Path("output")
    if not output_dir.exists():
        return []

    runs = []
    for d in sorted(output_dir.iterdir(), key=lambda p: p.name, reverse=True):
        summary_file = d / "summary.json"
        report_file = d / "report.xlsx"
        if summary_file.exists() and report_file.exists():
            summary = json.loads(summary_file.read_text(encoding="utf-8"))
            runs.append({
                "id": d.name,
                "data_file": summary.get("data_file", ""),
                "violation_rate": summary.get("violation_rate", ""),
                "total": summary.get("total_conversations", 0),
            })
        if len(runs) >= limit:
            break
    return runs
```

- [ ] **Step 5: 提交**

```bash
git add src/auto_qc/web/
git commit -m "feat: Web UI 任务提交、进度轮询、结果展示、历史记录"
```

---

### Task 8: 端到端验证

- [ ] **Step 1: 测试 CLI QC 子命令**

```bash
uv run auto-qc qc run --data "/c/Users/dongyi/Desktop/home/test-data/pi-500-data.xlsx" --rule-sets pi-rules 2>&1 | head -20
```

预期：质检流程正常运行。

- [ ] **Step 2: 测试 Web 启动**

```bash
uv run auto-qc web
# 浏览器打开 http://127.0.0.1:8000
```

预期：页面正常加载，质检和配置标签可用。

- [ ] **Step 3: 确认旧路径不再可用**

```bash
uv run python -c "from auto_qc.domain.rules import load_rule_sets" 2>&1
# 预期：ModuleNotFoundError
```

- [ ] **Step 4: 最终提交**

```bash
git add -A
git commit -m "feat: auto-qc 平台重构完成 — QC+PI 融合 + Web UI + 统一 CLI"
```
