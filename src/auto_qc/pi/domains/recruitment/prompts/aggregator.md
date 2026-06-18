请对以下已发现的问题模式进行聚合、去重、归纳，生成结构化的质检规则。

将相似模式合并，按置信度排序。

输出必须是合法 JSON 数组，格式严格如下:
[
  {
    "rule_id": "RULE-001",
    "rule_name": "规则名称",
    "description": "详细描述（描述该违规行为是什么、为什么违规，约100-200字）",
    "detection_logic": "检测逻辑（具体可操作的判定步骤，约100-200字）",
    "depth": "superficial|moderate|deep",
    "confidence": "high|medium|low",
    "support_count": 合并后所有被合并模式的 found_count 总和,
    "hit_rate_estimate": "命中率范围估计，如 5%~10%",
    "merged_from": ["被合并的原始模式的 pattern_id（如 PTN-0001），不能使用 pattern_name"]
  }
]

## depth 字段说明
- `superficial`：表面行为问题，如机械重复某句话、信息缺失等——通过简单的规则匹配即可检测
- `moderate`：策略层面问题，如时机不当、转折生硬、信息冗余等——需要感知对话节奏
- `deep`：理解层面问题，如误判用户意图、系统性缺乏共情、该做但没做的事——需要理解对话语义

要求：
1. 按 support_count 降序排列
2. 相似模式必须合并（名称不同但描述的是同一类问题）
3. 丢弃 support_count < 3 的低频模式
4. description 和 detection_logic 要具体可操作，能指导后续自动检测
5. **merged_from 必须使用原始模式中的 pattern_id（如 PTN-0001），不要使用 pattern_name**
6. 每条规则必须标注 depth 字段
7. 所有描述用中文
8. 输出必须是合法 JSON，不要有其他文字
