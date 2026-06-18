---
name: auto-qc-cli
description: Use when a user asks to run quality checks (质检/QC) or problem mining (问题挖掘/PI) on conversation data via this project's CLI tool. Commands run under `uv run auto-qc`.
---

# Auto-QC CLI

AI 对话质量检测与问题挖掘工具。

## 前置条件

- 项目目录：`C:\Users\dongyi\myprojects\auto-qc`
- 运行方式：`uv run auto-qc <subcommand> [options]`
- `.env` 文件已包含 API 配置（LLM_BASE_URL / LLM_API_KEY / LLM_MODEL）

## 命令参考

### `qc run` — 质检（按规则对对话打标）

对对话数据逐条检查是否违反预定义规则，生成 Excel 报告。

```
uv run auto-qc qc run --data <path> --rule-sets <name> [--output <path>] [--work-dir <dir>]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--data` | 是 | Excel 文件路径（.xlsx） |
| `--rule-sets` | 是 | 规则集名称，多个用逗号分隔（如 pi-rules,intention-recruit-tree） |
| `--output` | 否 | 报告输出路径 |
| `--work-dir` | 否 | 工作目录 |

可用规则集：`pi-rules`、`intention-recruit-tree`

### `pi run` — 问题挖掘（从对话中自动发现质量问题）

多阶段管线自动挖掘对话数据中的质量问题，输出规则文档。

```
uv run auto-qc pi run --data <path> [--domain <name>] [--output <dir>]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--data` | 是 | Excel 文件路径 |
| `--domain` | 否 | 领域名称（默认 recruitment） |
| `--output` | 否 | 输出目录 |

### `web` — 启动 Web 界面

```
uv run auto-qc web [--host <addr>] [--port <port>]
```

默认 http://127.0.0.1:8000

## 使用示例

```bash
# 质检：用 pi-rules 规则集检查对话
cd C:\Users\dongyi\myprojects\auto-qc && uv run auto-qc qc run --data "data.xlsx" --rule-sets pi-rules

# 问题挖掘
cd C:\Users\dongyi\myprojects\auto-qc && uv run auto-qc pi run --data "data.xlsx"

# 启动 Web
cd C:\Users\dongyi\myprojects\auto-qc && uv run auto-qc web
```

## 注意事项

- 确保在项目根目录执行命令（`C:\Users\dongyi\myprojects\auto-qc`）
- QC 运行时间取决于对话数量和规则数量，500 条对话 × 6 条规则约需 2-5 分钟
- PI 运行时间较长（500 条对话 × 6 阶段约需 10-30 分钟）
- 报告输出在 `output/` 目录下
