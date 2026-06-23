---
name: auto-qc-cli
description: Use when a user asks to run quality checks (质检/QC) or problem mining (问题挖掘/PI) on conversation data via this project's CLI tool. Commands run under `uv run auto-qc`.
---

# Auto-QC CLI

AI 对话质量检测与问题挖掘工具。Web 和 CLI 功能完全同步。

## 前置条件

- 项目目录：`C:\Users\dongyi\myprojects\auto-qc`
- 运行方式：`uv run auto-qc <subcommand> [options]`
- `.env` 文件已包含 API 配置

## 命令参考

### `qc run` — 运行质检

```
uv run auto-qc qc run --data <path> --rule-sets <name>
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--data` | 是 | Excel 文件路径（.xlsx） |
| `--rule-sets` | 是 | 规则集名称，多个用逗号分隔 |
| `--output` | 否 | 报告输出路径 |
| `--work-dir` | 否 | 工作目录 |

### `qc history` — 查看/删除质检历史

```
uv run auto-qc qc history                 # 列出最近 10 条
uv run auto-qc qc history --limit 20      # 指定条数
uv run auto-qc qc history --delete <id>   # 删除指定记录
```

### `qc download <id>` — 下载质检报告

```
uv run auto-qc qc download <id> -o ./reports
```

### `pi run` — 运行问题挖掘

```
uv run auto-qc pi run --data <path>
```

### `pi history` — 查看/删除挖掘历史

```
uv run auto-qc pi history
uv run auto-qc pi history --delete <id>
```

### `pi download <id>` — 下载挖掘结果

```
uv run auto-qc pi download <id>
```

### `config` — 查看/修改 LLM 配置

```
uv run auto-qc config show                # 查看当前配置
uv run auto-qc config set --model xxx     # 修改模型
uv run auto-qc config set --api-key xxx   # 修改 API Key
```

### `web` — 启动 Web 界面

```
uv run auto-qc web [--host 127.0.0.1] [--port 8000]
```

## 使用示例

```bash
cd C:\Users\dongyi\myprojects\auto-qc
uv run auto-qc qc run --data "data.xlsx" --rule-sets pi-rules
uv run auto-qc pi run --data "data.xlsx"
uv run auto-qc config show
uv run auto-qc web
```

## 注意事项

- 必须在项目根目录执行
- QC 50 条对话 × 6 条规则约 2-5 分钟
- PI 500 条对话约 10-30 分钟
- Excel 文件不需要「意向结果」列
