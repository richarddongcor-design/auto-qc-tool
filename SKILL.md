---
name: auto-qc
description: 外呼通话对话文本质检。指定数据 Excel 和规则文件，自动完成合规检测，输出 Excel 报告。
---

# auto-qc 外呼通话文本质检

## 触发方式

用户输入：`/auto-qc --data <excel路径> --rules <规则路径> [--rules-name <名称>] [--output <报告路径>]`

## 执行方式

1. 检查当前目录 `./auto_qc/` 是否存在
   - 不存在 → 从 Skill 包复制代码到 `./auto_qc/`
   - 存在 → 对比版本，Skill 版本更高则覆盖更新
2. `cd ./auto_qc/ && uv sync && uv run -m auto_qc.cli <传递所有参数>`

## 运行模式

| 命令 | 行为 |
|------|------|
| `--data <路径> --rules <路径>` | 规则文件首次运行，自动缓存 |
| `--data <路径> --rules-name <名称>` | 使用已缓存的规则 |

## 参数

- `--data`（必需）：源数据 Excel 文件路径
- `--rules`（可选）：合规规则 Markdown 文件路径（不传时需传 `--rules-name`）
- `--rules-name`（可选）：规则名称，用于缓存命名和后续引用
- `--output`：报告输出路径
