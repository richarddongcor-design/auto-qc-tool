"""招聘场景数据适配器。

将 Excel + 双层 JSON 格式的数据转换为框架标准格式（DialogueRecord 列表）。
处理招聘特有的细节：TTS 符号过滤、用户挂机检测。
"""
import json
import logging
import re
from pathlib import Path
from auto_qc.pi.domains.base import DataAdapter, DialogueRecord, DialogueTurn

logger = logging.getLogger(__name__)

# TTS 控制符号正则：匹配 [xxx]、/xxx/ 等短格式标记
# 限制 /xxx/ 最多 3 个字符，防止误删实质性内容（如 /这个工作地点在福州福清市/）
TTS_SYMBOL_PATTERN = re.compile(r"\[.*?\]|/[^/\s]{0,3}/")

# 用户挂机特征关键词
HANGUP_KEYWORDS = ["通话结束", "挂机", "电话已挂断", "通话已终止"]


class RecruitmentAdapter(DataAdapter):
    """Excel 双层 JSON -> DialogueRecord 列表的转换器。"""

    def parse(self, input_path: str) -> list[DialogueRecord]:
        """解析 Excel 文件，返回 DialogueRecord 列表。

        Excel 列要求：id, 时间, 对话文本（双层 JSON）, 意向结果（保留）
        """
        import pandas as pd

        df = pd.read_excel(input_path)
        records = []
        errors = []

        for _, row in df.iterrows():
            dialogue_id = str(row.get("通话id", row.get("id", "")))
            conv_text = str(row.get("对话文本", ""))

            try:
                turns = self._parse_turns(conv_text)
                if turns:
                    records.append(DialogueRecord(id=dialogue_id, turns=turns))
                else:
                    errors.append(f"{dialogue_id}: 无有效轮次")
            except Exception as e:
                errors.append(f"{dialogue_id}: {e}")

        if errors:
            logger.warning(f"解析到 {len(errors)} 条错误: {errors[:5]}")

        logger.info(f"成功解析 {len(records)} 条对话，跳过 {len(errors)} 条")
        return records

    def format_for_llm(self, record: DialogueRecord) -> str:
        """将 DialogueRecord 转为 LLM 可读文本。"""
        lines = [f"对话ID: {record.id}"]
        for i, turn in enumerate(record.turns, 1):
            role_label = "AI" if turn.role == "ai" else "用户"
            lines.append(f"  第{i轮} [{role_label}]: {turn.content}")
        return "\n".join(lines)

    def preprocess_text(self, text: str) -> str:
        """过滤 TTS 控制符号。

        去除 [xxx]、/xxx/ 等 TTS 引擎标记，让分析文本更干净。
        原始文本仍保留在 DialogueRecord 中用于引用展示。
        """
        return TTS_SYMBOL_PATTERN.sub("", text).strip()

    def is_user_hangup(self, record: DialogueRecord) -> bool:
        """判断是否用户主动挂机。

        检测逻辑：最后两轮中，如果用户发言为空（或"用户无应答"），
        且 AI 发言包含挂机关键词，则判定为用户挂机。
        """
        if len(record.turns) < 2:
            return False

        last_turns = record.turns[-2:]
        for turn in last_turns:
            if turn.role == "ai" and any(
                kw in turn.content for kw in HANGUP_KEYWORDS
            ):
                return True
        return False

    def _parse_turns(self, conv_text: str) -> list[DialogueTurn]:
        """双层 JSON 解析：先解析对话文本，再提取 ttsResult/asrResult。"""
        try:
            data = json.loads(conv_text)
        except json.JSONDecodeError:
            return []

        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return []

        if not isinstance(data, list):
            return []

        turns = []
        for turn_data in data:
            if not isinstance(turn_data, dict):
                continue

            tts = turn_data.get("ttsResult", "")
            asr = turn_data.get("asrResult", "")

            if tts:
                turns.append(DialogueTurn(role="ai", content=str(tts)))
            if asr:
                turns.append(DialogueTurn(role="user", content=str(asr)))

        return turns
