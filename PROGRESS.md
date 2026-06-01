# auto-qc 项目进展

## 2026-06-01

### 并发协调器改造（已完成）

将 Worker 并发控制从 SKILL.md 软约束改为 coordinator.py 代码硬兜底。

**核心改动：**

| 文件 | 变更 |
|------|------|
| `scripts/coordinator.py` | **新增**，5 个子命令（init / get-next / mark-done / mark-failed / summary），`MAX_CONCURRENCY=10` 硬限制 |
| `scripts/test_coordinator.py` | **新增**，6 个单元测试全部通过，包含 50 批次完整调度集成测试 |
| `SKILL.md` | Step 2/3/5 改用 coordinator.py 调度并发，不再由 Claude 自行计数 |

**关键机制：**
- `get-next` 原子性标记 running 防重复领取
- 校验失败的批次重置为 pending，下次 get-next 自动重新领取
- retry_count >= 3 时 mark-failed 记录到 failed_batches

**验证结果：** 6/6 单元测试通过 + 手动验证 get-next 原子性（10 批后 slots=0，mark-done 后自动释放新槽位）

### 完成事项
- ✅ 头脑风暴完成（brainstorming skill）
- ✅ 设计文档完成：`docs/superpowers/specs/2026-06-01-auto-qc-design.md`
- ✅ 实施计划完成：`docs/superpowers/plans/2026-06-01-auto-qc-implementation.md`
- ✅ 并发协调器设计：`docs/superpowers/specs/2026-06-01-coordinator-design.md`
- ✅ 并发协调器实施计划：`docs/superpowers/plans/2026-06-01-coordinator-plan.md`
- ✅ coordinator.py 实现 + 6 个单元测试
- ✅ SKILL.md Step 2/3/5 改造为 coordinator 调度

### 关键决策
1. **架构**：统一框架 + 两套规则（合规检测由用户提供规则，归因分析内置规则）
2. **技术路线**：Prompt 驱动 + 少量 Python I/O，质检判断全部由 Claude sub-agent 完成
3. **批次**：每批 100 条/Worker，并发协调器硬限制最多 10 个 Worker 同时运行
4. **校验**：数量校验 + 格式校验 + 1-2% 分层交叉验证
5. **Harness 范式**：Worker 防偷懒约束（逐条输出、规则遍历清单、抽检内嵌）
6. **归因**：按意向结果类别分组（B/C/E/F/I），每组下列出归因原因类别和占比
7. **运行模式**：默认全量（合规+归因），`--no-attribution` 仅合规，`--attribution-only` 仅归因
8. **可移植性**：skill zip 解压到 `~/.agents/skills/auto-qc/` 即可使用
9. **并发兜底**：coordinator.py 代码硬兜底（非 Claude 自行计数），防止并发失控

### 待办事项
- [x] Task 9: 端到端测试完成 ✅
- [x] 报告质量优化（2026-06-01 18:00）
- [x] Skill 全面审查和代码质量修复（2026-06-01 19:30）

### Skill 全面审查（2026-06-01 19:30）

用户对 SKILL.md 执行流程提出质疑："多次重复运行"、进度丢失。
通过全面审查发现问题并修复：

**发现的问题（0 Critical, 2 Medium, 5 Low）：**

| 严重度 | 文件 | 问题 |
|--------|------|------|
| Medium | `report_writer.py:cleanup_temp_files` | 不清理 `attribution_batches/` 目录 |
| Medium | `requirements.txt` vs 设计文档 | json-repair 版本不一致 |
| Low | `data_loader.py:load_excel` | Excel 文件损坏时出原始 traceback |
| Low | `rules_parser.py:main` | 无文件存在性检查 |
| Low | `data_loader.py:save_progress` | 首次创建时不设置 `started_at` |
| Low | `report_writer.py:main` | 可选文件不存在时静默跳过 |
| Low | `test_report_writer.py` | 缺少 attribution_batches 清理测试 |

**SKILL.md 不一致修复：**

| 修复 | 说明 |
|------|------|
| 进度追踪 | 增加 `batch_status` 三态（pending/running/done/failed） |
| 重试计数 | 增加 `retry_count` 字段 |
| 文件命名 | 统一为 `batch_N_result.json`，禁止其他后缀 |
| 断点续跑 | "running" 批次重置为 "pending" 并重跑 |
| 新增参数 | `--output`、`--attribution-only` |
| 默认输出 | 数据文件同目录 + 时间戳 |
| 模式支持 | 全量/仅合规/仅归因三种运行模式 |

**测试结果：** 13/13 tests pass（+1 新测试）

### 报告质量优化（2026-06-01 18:00）

用户反馈三项问题：
1. 合规检测证据片段只有 Worker 总结描述，缺少用户原话引用
2. 归因分析典型案例列为空，改进建议模板化（"针对XX类别，建议优化"）
3. 统计概览 rules_hit 以 JSON 字符串存储，不直观

**修复内容：**

| 文件 | 变更 |
|------|------|
| `templates/worker-prompt.md` | 新增第 2 条证据引用要求：evidence 字段必须直接引用对话原文，格式为 "用户: [原话] \| AI: [回应]" |
| `templates/worker-prompt.md` | 新增第 3 条改进建议要求：suggestion 必须针对具体对话问题给出可操作建议 |
| `templates/attribution-prompt.md` | 新增工作要求 4-5：典型案例必须从 whys 中提取 2-3 个真实对话原文；改进建议必须具体可操作 |
| `templates/attribution-prompt.md` | 输出格式新增 `examples` 和 `suggestion` 字段到每个归因类别 |
| `scripts/report_writer.py` | Sheet 3 统计概览从 JSON 字符串改为规则明细表（规则ID/名称/命中次数/占比 + 合计行） |
| `scripts/test_report_writer.py` | 新增 `test_stats_table_format` 测试验证表格格式 |

**测试结果：** 4/4 tests pass

### 端到端测试结果（2026-06-01 17:33）

使用真实数据完成 500 条对话质检全流程：

**合规检测结果：**
- 500 条对话，5 个 Worker 并发完成
- 违规率 80.6%（403/500）
- 通过 97 条
- 最高频违规：R10(结束话术乱码) 281次、R02(对话未正常结束) 128次、R05(回避用户问题) 103次

**归因分析结果：**
- 370 条非 A 意向对话，4 批次分析
- I(开场白挂断): 主要归因 用户无实质性回应(50.3%)、AI质量缺陷(49.7%)
- F(无意向): 主要归因 AI质量缺陷(97.7%)、用户明确拒绝(91.5%)
- B(不确定): 主要归因 AI质量缺陷(79.2%)、用户询问细节但意向不明(45.5%)

**报告输出：** `C:\Users\dongyi\Desktop\pi-500-data_质检报告.xlsx`（53K）
- Sheet 1 合规检测: 768 行（每条违规一条记录）
- Sheet 2 归因分析: 25 行（5 个意向类别 × 归因类别）
- Sheet 3 统计概览: 总对话数、违规率、规则命中、严重程度分布

### 实施完成（2026-06-01 16:30）

所有代码文件已创建并通过测试：

| Task | 文件 | 状态 |
|------|------|------|
| 1 | requirements.txt + 目录结构 | ✅ |
| 2 | data_loader.py + test_data_loader.py | ✅ 5 tests pass |
| 3 | report_writer.py + test_report_writer.py | ✅ 3 tests pass |
| 4 | rules_parser.py + test_rules_parser.py | ✅ 3 tests pass |
| 5 | templates/attribution-rules.md | ✅ |
| 6 | templates/worker-prompt.md | ✅ |
| 7 | templates/attribution-prompt.md | ✅ |
| 8 | SKILL.md | ✅ |

**端到端验证通过：**
- rules_parser: 13 条规则从真实 rules.md 解析成功
- data_loader: 500 条数据拆分为 5 批，对话预处理为 "AI: xxx / 用户: xxx" 格式
- 全部 13 个单元测试通过（+1 新增）

### 测试数据
- `C:\Users\dongyi\Desktop\pi-500-data.xlsx`：500 条对话数据
- `C:\Users\dongyi\myprojects\auto-pi\auto-pi\harness\output\2026-06-01_101119\phase5\rules.md`：13 条合规规则
