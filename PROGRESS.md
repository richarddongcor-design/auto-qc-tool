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
- [ ] Task 1: 创建 Skill 骨架 + requirements.txt
- [ ] Task 2: data_loader.py — Excel 读取 + 列匹配 + 对话预处理 + 批次拆分 + 进度管理
- [ ] Task 3: report_writer.py — 报告 Excel 输出 + 临时文件清理
- [ ] Task 4: rules_parser.py — Markdown 规则解析器
- [ ] Task 5: 内置归因规则 — attribution-rules.md
- [ ] Task 6: templates/worker-prompt.md — Worker 批处理打标模板
- [ ] Task 7: templates/attribution-prompt.md — 归因分析 Worker 模板
- [ ] Task 8: SKILL.md — Skill 主文件（核心）
- [ ] Task 9: 端到端测试 — 500 条数据验证

### 测试数据
- `C:\Users\dongyi\Desktop\pi-500-data.xlsx`：500 条对话数据
- `C:\Users\dongyi\myprojects\auto-pi\auto-pi\harness\output\2026-06-01_101119\phase5\rules.md`：13 条合规规则
