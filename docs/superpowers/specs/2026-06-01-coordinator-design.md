# 并发协调器设计（coordinator.py）

## Context

当前 Worker 并发控制依赖 SKILL.md 的文字指令（"并发不超过 10 个"），由 Claude 自行计数。这是一个软约束——如果 Claude 数错或 prompt 理解偏差，可能导致并发失控。需要代码兜底。

## 方案概述

新增 `coordinator.py` 脚本，Claude 每轮启动 Worker 前调用脚本获取"本轮应该启动哪些批次"。脚本读取 `progress.json`，硬性计算可用并发槽位，最多返回 10 个批次编号。

Claude 从"自己数"变成"问脚本要任务"，脚本掌握并发上限的决策权。

## 交互流程

```
Claude                          coordinator.py
  │                                 │
  │── get-next ───────────────────→│ 读取 progress.json
  │                                 │ 计算: running_count + pending_slots
  │                                 │ 返回: ["1", "2", "3", ...] (≤10)
  │← 返回批次列表 ─────────────────│
  │                                 │
  │ 启动 N 个 Agent (background)   │
  │ 收集结果...                     │
  │                                 │
  │── mark-done 1 ────────────────→│ 更新 batch_status["1"]="done"
  │── mark-done 2 ────────────────→│ ...
  │                                 │
  │── get-next ───────────────────→│ 计算新的可用槽位
  │← 返回下一批批次列表 ───────────│  (可能返回 5-6 个新批次)
  │                                 │
  │ ... 循环直到 done ...          │
```

## 脚本子命令

| 命令 | 说明 | 返回 |
|------|------|------|
| `get-next` | 获取本轮可启动的批次列表 | JSON: `{"batches": ["6","7","8"], "running": 3, "slots": 7}` |
| `mark-done <batch_id>` | 标记批次完成 | `{"ok": true, "completed": 23, "total": 100}` |
| `mark-failed <batch_id>` | 标记批次失败 | `{"ok": true, "failed": ["1"]}` |
| `summary` | 查看当前进度 | `{"completed": 80, "running": 10, "pending": 10, "failed": 0}` |

## coordinator.py 核心逻辑

**文件路径**: `~/.agents/skills/auto-qc/scripts/coordinator.py`

### get-next

1. 读取 `progress.json`
2. 统计当前 `running` 状态的批次数量
3. `available_slots = max_concurrency - running_count`（max_concurrency=10，写死在常量中）
4. 如果可用槽位 ≤ 0，返回空列表
5. 找出所有 `pending` 状态的批次（按编号升序），取前 `available_slots` 个
6. **同时**把选中的批次标记为 `running`，更新 `progress.json`
7. 返回批次编号列表

**关键点**：第 6 步在原子操作内完成——选中的批次立即标记为 `running`，防止同批次被重复领取。

### mark-done / mark-failed

- 读取 `progress.json`，更新对应批次状态为 `done` 或 `failed`
- 递增 `completed_batches` 计数器（mark-done）
- 更新 `updated_at` 时间戳
- 返回当前进度摘要

### summary

- 读取 `progress.json`，统计各状态批次数量并返回

## SKILL.md 改动

### Step 3（合规检测 Worker 分发）

**旧模式**：
> 并发不超过 10 个 Worker Agent，按批次逐批分发。

**新模式**：
> 使用 `coordinator.py` 脚本控制并发。循环执行以下操作：
> 1. 调用 `coordinator.py get-next` 获取本轮可启动的批次列表（脚本已硬性限制最多 10 个）
> 2. 如果返回空列表，退出循环（所有批次已处理完）
> 3. 对返回的每个批次：读取数据 + 规则 → 组合 Worker Prompt → 更新 `batch_status[N]` 为 `running`（脚本已做）→ 启动 Agent
> 4. 收集所有 Agent 结果后，对每个完成的批次调用 `coordinator.py mark-done <batch_id>`
> 5. 校验失败 → `coordinator.py mark-failed <batch_id>`，重试逻辑不变
> 6. 重复步骤 1

### Step 5（归因分析 Worker 分发）

**旧模式**：
> 同样并发不超过 10 个 Worker Agent 逐批归因

**新模式**：
> 同样使用 `coordinator.py` 脚本控制并发，流程同 Step 3。

## 进度文件字段变更

`progress.json` 的 `batch_status` 新增 `running` 状态（已有 `pending`/`done`/`failed`）：

| 状态 | 含义 |
|------|------|
| `pending` | 等待处理 |
| `running` | coordinator.py 已分配给 Claude，正在处理中 |
| `done` | 处理完成 |
| `failed` | 重试 3 次后仍失败 |

## 并发安全保障

| 层级 | 机制 | 说明 |
|------|------|------|
| 代码（硬兜底） | coordinator.py | `MAX_CONCURRENCY = 10` 写死，脚本同时只返回 ≤10 个批次，且标记为 running 防重复 |
| 指令（辅助） | SKILL.md | Claude 按脚本返回的列表启动，不自行判断并发数 |

## 受影响的文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `scripts/coordinator.py` | **新增** | 并发协调器，管理批次状态和并发槽位 |
| `SKILL.md` | 修改 | Step 3、Step 5 的并发控制描述，改为调用 coordinator.py |
| `PROGRESS.md` | 修改 | 记录本次设计决策 |

## 验证方案

1. 用 mock 数据模拟 50 个批次的 progress.json
2. 连续调用 `get-next` 多次（不 mark-done），验证返回数量 ≤10 且批次不重复
3. 调用 `mark-done` 后再次 `get-next`，验证新批次被放出
4. 验证 `summary` 返回各状态数量正确
5. 验证断点续跑场景：progress.json 中有 running 状态的批次，脚本会将其重置为 pending
