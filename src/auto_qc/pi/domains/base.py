"""领域插件基类。

框架通过此接口与任何业务场景交互，框架代码不知道领域细节。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class DialogueTurn:
    """对话轮次：AI 或用户的一条发言。"""
    role: str       # "ai" | "user"
    content: str    # 原始发言文本


@dataclass
class DialogueRecord:
    """一条完整的对话记录。"""
    id: str
    turns: list[DialogueTurn]


@dataclass
class DomainPrompts:
    """各阶段所需的 prompt 模板。

    v3.0: explorers 支持多视角并行探索。
    每个元素为 (视角名称, prompt文本, temperature) 三元组。
    """
    explorers: list[tuple[str, str, float]]  # Phase 2: 多视角探索 [(name, prompt, temperature), ...]
    topic_clustering: str   # Phase 3: 主题聚类
    merge_clusters: str     # Phase 3: 跨批合并
    aggregator: str         # Phase 4: 规则生成
    verifier: str           # Phase 5: 规则验证


class DataAdapter(ABC):
    """将领域原始数据转换为框架标准格式。

    每个领域实现 parse() 和 format_for_llm() 两个方法。
    框架只认 DialogueRecord 列表，不认识 Excel/CSV/JSON 等业务格式。
    """

    @abstractmethod
    def parse(self, input_path: str) -> list[DialogueRecord]:
        """读取数据源 -> 返回 DialogueRecord 列表。"""
        ...

    @abstractmethod
    def format_for_llm(self, record: DialogueRecord) -> str:
        """将单条对话转为 Sub-Agent 可读的文本格式。"""
        ...

    def preprocess_text(self, text: str) -> str:
        """预处理文本（如过滤 TTS 控制符号），子类可覆盖。

        默认实现不做任何处理，领域实现可以过滤特定字符。
        """
        return text


class DomainPlugin(ABC):
    """领域插件抽象基类。

    每个领域实现一个子类，提供：
    - prompts: 五个阶段的 prompt 模板（通过 load_prompts() 从文件加载）
    - data_adapter: 数据格式转换器
    - quality_criteria: 领域特有的质检标准（可选）
    """

    name: str = "base"
    _prompts_cache: DomainPrompts | None = None

    @property
    def prompts(self) -> DomainPrompts:
        """返回当前领域的五个阶段 prompt 模板（惰性加载，缓存结果）。"""
        if self._prompts_cache is None:
            self._prompts_cache = self.load_prompts()
        return self._prompts_cache

    def load_prompts(self) -> DomainPrompts:
        """从 prompt 文件加载所有 prompt 模板。

        子类可覆盖此方法实现自定义加载逻辑。
        默认使用 FilePromptLoader 从 domains/<name>/prompts/ 读取 .md 文件。
        """
        from auto_qc.pi.core.prompt_loader import FilePromptLoader

        loader = FilePromptLoader(domain=self.name)
        return loader.to_domain_prompts()

    @property
    @abstractmethod
    def data_adapter(self) -> DataAdapter:
        """返回数据格式适配器。"""
        ...

    @property
    def quality_criteria(self) -> dict:
        """返回领域特有的质检标准（可选覆盖）。

        例如: {"no_response_threshold": 3, "filter_tts_symbols": True}
        框架不解释这些值，只传递给领域自己的 prompt 和适配器。
        """
        return {}
