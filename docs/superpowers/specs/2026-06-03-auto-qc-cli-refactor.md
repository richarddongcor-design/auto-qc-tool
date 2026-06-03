# auto-qc CLI 重构设计文档

**日期**: 2026-06-03
**状态**: Draft
**基于**: 2026-06-01 初始设计文档（Prompt 驱动架构）

---

## 1. 核心理念

**代码做框架，约束每一步；LLM 只做分析，不参与流程控制。**

### 1.1 三个角色

| 角色 | 谁做 | 职责 |
|------|------|------|
| 用户入口 | SKILL.md（薄壳） | 用户在 Claude Code 输入 `/auto-qc`，触发 Python CLI |
| 流程框架 | Python CLI | 驱动全流程、并发控制、输入输出校验、交叉验证、进度管理 |
| 语义分析 | LLM（Anthropic SDK） | 理解对话、按规则判断违规、输出推理链 |

### 1.2 两个分层

**框架层（纯工程，与质检业务无关）**：

| 模块 | 职责 |
|------|------|
| `coordinator.py` | 并发控制：原子化状态管理，硬限制最大并发数 |
| `worker.py` | LLM API 调用封装：发 prompt、收 JSON、过滤 thinking block、json_repair |
| `validator.py` | 通用契约校验：字段完整性、数量匹配、JSON 合法性 |
| `progress.py` | 进度读写：批次状态、重试次数、阶段管理 |
| `cross_validator.py` | 抽样对比引擎：分层抽样、规则级对比、差异率计算 |
| `orchestrator.py` | 步骤串联：step1→2→3→4→5→6→7，不碰业务数据 |

**领域层（质检业务逻辑）**：

| 模块 | 职责 |
|------|------|
| `rules.py` | 规则文件解析 + 校验（R01/R02、severity、detection_logic） |
| `prompts.py` | Prompt 模板组装：规则 + 对话 → LLM 可理解的完整 prompt |
| `schemas.py` | 结果数据结构定义（TypedDict/dataclass）：violations、evidence 等字段 |
| `report.py` | Excel 报告生成：3 个 sheet、列定义、颜色标注 |
| `data_loader.py` | 对话预处理（TTS/ASR JSON → "AI: xxx / 用户: xxx"）+ 批次拆分 |
| `attribution.py` | 内置归因规则（A01-A06）+ 归因 prompt 组装 |

---

## 2. 项目结构

```
auto-qc/
├── SKILL.md                          # 薄壳入口（~10行）
├── pyproject.toml                    # uv 项目配置 + 依赖 + CLI entry point
│
├── src/auto_qc/
│   ├── __init__.py                   # 版本号 VERSION
│   │
│   ├── framework/                    # 框架层：纯工程逻辑
│   │   ├── orchestrator.py           # 全流程编排
│   │   ├── coordinator.py            # 并发控制
│   │   ├── worker.py                 # LLM API 调用封装
│   │   ├── validator.py              # 输入/输出契约校验
│   │   ├── cross_validator.py        # 交叉验证引擎
│   │   └── progress.py               # 进度管理
│   │
│   └── domain/                       # 领域层：质检业务逻辑
│       ├── rules.py                  # 规则解析 + 校验
│       ├── prompts.py                # Prompt 模板组装
│       ├── schemas.py                # 结果数据结构
│       ├── report.py                 # Excel 报告生成
│       ├── data_loader.py            # 对话预处理 + 批次拆分
│       └── attribution.py            # 内置归因规则
│
├── templates/                        # Prompt 模板 + 内置规则
│   ├── worker-prompt.md              # 合规检测 Worker 模板
│   ├── attribution-prompt.md         # 归因分析模板
│   └── attribution-rules.md          # 内置归因规则（A01-A06）
│
├── tests/
│   ├── framework/                    # 框架层测试
│   │   ├── test_coordinator.py
│   │   ├── test_validator.py
│   │   ├── test_cross_validator.py
│   │   └── test_worker.py
│   └── domain/                       # 领域层测试
│       ├── test_rules.py
│       ├── test_data_loader.py
│       └── test_report.py
│
└── docs/superpowers/specs/           # 设计文档
```

---

## 3. 安装与初始化

### 3.1 首次运行

```
用户输入: /auto-qc --data xxx.xlsx --rules xxx.md
         ↓
  SKILL.md 引导 Claude 执行初始化脚本
         ↓
  检查当前工作目录 ./auto_qc/ 是否存在
  ├── 不存在 → 从 Skill 包复制 src/ 和 templates/ 到 ./auto_qc/
  └── 存在 → 读 ./auto_qc/VERSION，对比 Skill 包版本
             ├── 相同 → 跳过
             └── Skill 版本更高 → 覆盖更新 src/ 和 templates/
         ↓
  cd ./auto_qc/ && uv sync && uv run -m auto_qc.cli ...
```

### 3.2 运行时环境

- 环境变量自动继承自 Claude Code（`ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_MODEL`）
- 用户零配置：不传 API key、不写配置文件

---

## 4. LLM 调用

### 4.1 方式

使用 Anthropic 官方 Python SDK，通过 `base_url` 参数指向用户的中转服务：

```python
from anthropic import Anthropic

client = Anthropic(
    base_url=os.environ["ANTHROPIC_BASE_URL"],
    api_key=os.environ["ANTHROPIC_AUTH_TOKEN"],
)

response = client.messages.create(
    model=os.environ["ANTHROPIC_MODEL"],
    max_tokens=2000,
    messages=[...],
)
```

不收 HTTP 裸拼请求，SDK 自动处理重试、超时、错误。

### 4.2 Thinking Block 过滤

DeepSeek 等模型会在 response.content 中返回 `ThinkingBlock`（思维链），worker 只取 `TextBlock` 的文字内容：

```python
from anthropic.types import TextBlock

for block in response.content:
    if isinstance(block, TextBlock):
        return block.text
```

### 4.3 并发

最多 5 个并发 API 请求（`asyncio.gather` + `Semaphore`），由 `coordinator.py` 原子化控制。

---

## 5. Harness 验证体系

每个步骤都有输入验证和输出验证，验证不通过立即停止（快速失败）。

### 5.1 验证清单

| 步骤 | 输入验证 | 输出验证 |
|------|----------|----------|
| 环境检查 | 依赖安装？文件存在？ | — |
| 规则解析 | 文件可读？ | 规则 ID 唯一？severity 合法？字段完整？规则数量 > 0？ |
| 数据加载 | Excel 可读？列匹配成功？ | total > 0？批次文件数量 = 预期？每条数据含 id/conversation？ |
| Worker 分发 | 批次文件存在？prompt 模板有效？ | 结果数 == 批次大小？每条有 id？rules_checked 齐全？JSON 合法？ |
| 交叉验证 | 违规/非违规样本都有？ | 差异率计算完成？差异率 > 阈值则标记 |
| 归因分析 | 归因规则有效？过滤后数据 > 0？ | 同上 Worker 分发 |
| 报告生成 | 原始总数已知？ | 合并后总数 == 原始总数？报告文件存在且 > 0 字节？ |

### 5.2 验证失败处理

- Step 1-2（环境/规则）：直接报错退出，提示用户修复
- Step 4-6（Worker）：自动重试最多 3 次，3 次仍失败记入 `failed_batches.json`，不阻塞流程
- Step 7（报告）：合并失败则保留已完成的中间结果，不让数据丢失

---

## 6. 规则管理

### 6.1 合规规则（用户提供）

- 用户通过 `--rules` 参数提供 Markdown 规则文件
- 格式：`## R01: 规则名称` → `**严重程度**: 高/中/低` → `**描述**: ...` → `**检测逻辑**: ...`
- 系统不内置任何合规规则，不做假设
- `rules_parser.py` 解析为 JSON 规则包，`validator.py` 校验完整性

### 6.2 归因规则（内置）

- 系统内置 6 类归因规则（A01-A06），存放在 `templates/attribution-rules.md`
- 用户可通过 `--attribution-rules` 参数覆盖默认归因规则
- `--no-attribution` 可关闭归因分析

---

## 7. 交叉验证

`cross_validator.py` 代码自动执行，不是 Claude 手动抽样：

1. 统计整体违规率
2. 分层抽样：违规组抽 2%，非违规组抽 1%
3. 将抽中样本重新发给 LLM 做 double-check
4. 规则级对比：同一条对话同一个规则，两次判断是否一致
5. 差异率 = 不一致的规则判断数 / 总对比规则数
6. 阈值处理：
   - < 5%：正常
   - 5%-10%：标记可疑，结果中标注
   - > 10%：扩大抽样到 5%，重新对比

---

## 8. 执行流程（7 步）

```
Step 0: 初始化检测（首次使用自动解压代码 / 版本更新覆盖）
Step 1: 环境检查（依赖、文件、模板完整性）
Step 2: 规则解析 + 校验（仅合规检测模式）
Step 3: 数据加载 + 批次拆分（每批 100 条）
Step 4: 分发 Worker 并发质检（coordinator 控制并发上限 + validator 校验结果）
Step 5: 交叉验证（分层抽样 → 重新 LLM 判断 → 对比差异率）
Step 6: 归因分析（过滤非 A 意向 → 注入内置归因规则 → Worker 分发）
Step 7: 报告生成 + 清理
```

### 8.1 运行模式

| 命令 | 行为 |
|------|------|
| `--data <路径> --rules <路径>` | 合规检测 + 归因分析 |
| `--data <路径> --rules <路径> --no-attribution` | 仅合规检测 |
| `--data <路径> --attribution-only` | 仅归因分析（内置规则） |

---

## 9. Worker 防偷懒机制

不靠 Prompt 喊话，用工程结构约束：

1. **逐条输出强制**：每条对话必须独立输出，含唯一 `id`。validator 校验：结果数 == 批次大小
2. **规则遍历证明**：Worker 输出必须含 `rules_checked: ["R01","R02",...]` 字段，证明确实每条规则都过了一遍
3. **抽检内嵌**：Worker 随机选 3-5 条对话，输出完整推理链（`spot_check_details`：不仅给结论，写清楚为什么）
4. **证据溯源**：每条违规结论必须含 `evidence` 字段（对话原文片段），能翻回原始对话验证

---

## 10. 进度与断点续跑

`progress.json` 数据结构：

```json
{
  "version": "0.1.0",
  "total_batches": 50,
  "completed_batches": 23,
  "phase": "qc",
  "batch_status": {"1": "done", "2": "running", "3": "pending", ...},
  "retry_count": {"1": 0, "2": 0, ...},
  "failed_batches": [],
  "started_at": "2026-06-03T10:00:00",
  "updated_at": "2026-06-03T10:15:00"
}
```

- 重启时自动检测 `progress.json`，提示用户继续或重来
- `batch_status` 为 `running` 的批次（上次中断）重置为 `pending` 重跑

---

## 11. 输出报告

3 个 Sheet 的 Excel 文件：

| Sheet | 内容 |
|-------|------|
| 合规检测 | id / 时间 / 意向结果 / 违规规则 / 问题类型 / 危害程度 / 证据片段 / 改进建议 |
| 归因分析 | 意向结果 / 归因类别 / 占比 / 数量 / 典型案例 / 改进建议 |
| 统计概览 | 总对话数 / 通过数 / 违规率 + 规则命中明细表 |

---

## 12. 依赖管理

- Python 环境管理：`uv`
- 依赖锁死版本（`pyproject.toml` + `uv.lock`）
- 核心依赖：`anthropic`、`openpyxl`、`pandas`、`json-repair`

---

## 13. 可测试性

每个模块独立可测：

- **框架层**：mock LLM 响应，测试并发控制、校验逻辑、交叉验证、进度读写
- **领域层**：mock 数据，测试规则解析、prompt 组装、报告生成、对话预处理
