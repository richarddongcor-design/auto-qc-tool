# auto-qc 项目进展

## 2026-06-01

### 完成事项
- ✅ 头脑风暴完成（brainstorming skill）
- ✅ 设计文档完成：`docs/superpowers/specs/2026-06-01-auto-qc-design.md`
- ✅ 实施计划完成：`docs/superpowers/plans/2026-06-01-auto-qc-implementation.md`

### 关键决策
1. **架构**：统一框架 + 两套规则（合规检测由用户提供规则，归因分析内置规则）
2. **技术路线**：Prompt 驱动 + 少量 Python I/O，质检判断全部由 Claude sub-agent 完成
3. **批次**：每批 100 条/Worker，每次并发 5 个 Worker
4. **校验**：数量校验 + 格式校验 + 1-2% 分层交叉验证
5. **Harness 范式**：Worker 防偷懒约束（逐条输出、规则遍历清单、抽检内嵌）
6. **归因**：按意向结果类别分组（B/C/E/F/I），每组下列出归因原因类别和占比
7. **运行模式**：默认全量（合规+归因），`--no-attribution` 仅合规，`--attribution-only` 仅归因
8. **可移植性**：skill zip 解压到 `~/.agents/skills/auto-qc/` 即可使用

### 待办事项
- [x] Task 9: 端到端测试完成 ✅
- [x] 报告质量优化（2026-06-01 18:00）

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
- 全部 11 个单元测试通过

### 测试数据
- `C:\Users\dongyi\Desktop\pi-500-data.xlsx`：500 条对话数据
- `C:\Users\dongyi\myprojects\auto-pi\auto-pi\harness\output\2026-06-01_101119\phase5\rules.md`：13 条合规规则
