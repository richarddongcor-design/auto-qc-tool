# Auto-QC-Tool 项目文档

质检 + 问题挖掘融合平台。Web 给人看，CLI 给 AI 调。

## 快速开始

```bash
uv run auto-qc web                                    # 启动 Web UI
uv run auto-qc qc run --data data.xlsx --rule-sets pi-rules   # CLI 质检
uv run auto-qc qc history                             # 查看质检历史
uv run auto-qc qc download <id>                       # 下载质检报告
uv run auto-qc pi run --data data.xlsx                # CLI 问题挖掘
uv run auto-qc pi history                             # 查看挖掘历史
uv run auto-qc config show                            # 查看 LLM 配置
uv run auto-qc config set --model xxx                 # 修改 LLM 配置
uv run pytest tests/ -x -q                            # 跑测试
```

## 项目结构

```
src/auto_qc/
├── cli.py          # 统一 CLI 入口（子命令: qc, pi, web）
├── core/           # 公共组件（LLM、配置管理）
│   ├── llm.py      # 统一 LLM 调用封装
│   └── config.py   # .env 读写
├── qc/             # 质检业务
│   ├── domain/     # 规则、数据加载、提示词、报告
│   └── framework/  # 编排器、校验器、工作线程
├── pi/             # 问题挖掘业务
│   ├── engine/     # 6 阶段管线
│   ├── agents/     # LLM 配置
│   ├── domains/    # 领域适配器
│   └── utils/      # Excel、JSON、报告工具
└── web/            # Web UI（FastAPI + Jinja2 + HTMX）
    ├── app.py      # FastAPI 工厂
    ├── routers/    # QC / PI / 配置 路由
    └── templates/  # 页面 + HTMX 片段
rules/              # JSON 规则集
output/             # 运行输出（报告 + 日志）
```

## 关键依赖

- Python >= 3.13, uv 管理
- fastapi + uvicorn + Jinja2 + HTMX（Web）
- openai（DeepSeek 兼容 API）
- openpyxl（Excel 读写）
- json-repair（容错 JSON 解析）

## 配置

`.env` 文件（项目根目录）：

```
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=sk-xxx
LLM_MODEL=deepseek-v4-flash
```

Web UI 的配置页可直接编辑保存。

## 规则集

规则集是 `rules/` 下的 JSON 文件，JSON 可通过配置页的界面编辑（计划中），当前可直接改 JSON。

运行时可指定规则集（`--rule-sets`），支持多个逗号分隔。

## 注意事项

- 500 条对话 × 6 条规则约 2-5 分钟，消费约 100 万 token
- PI 运行时间较长（6 阶段管线，约 10-30 分钟）
- 无数据库，状态存文件系统（output/ 目录）
- 单用户场景，任务状态存内存
