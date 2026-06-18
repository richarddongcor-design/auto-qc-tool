"""Prompt 文件加载器。

从 auto_qc.pi/domains/<domain>/prompts/ 目录读取 .md 文件，
按名称映射到各个 Phase 的 prompt 模板。
"""
from pathlib import Path
from dataclasses import dataclass
from auto_qc.pi.domains.base import DomainPrompts

PROMPT_MAP = {
    "explorers": {                          # Phase 2: 多视角探索
        "合规红线": "compliance.md",
        "对话流畅度": "flow.md",
        "语用适当性": "pragmatic.md",
        "逻辑一致性": "logic.md",
        "招聘专业度": "recruitment.md",
    },
    "topic_clustering": "topic_clustering.md",  # Phase 3
    "merge_clusters": "merge_clusters.md",  # Phase 3: 跨批合并
    "aggregator": "aggregator.md",       # Phase 4
    "verifier": "verifier.md",           # Phase 5: 规则验证
}

# 各视角对应的 temperature
EXPLORER_TEMPERATURES = {
    "合规红线": 0.1,
    "对话流畅度": 0.3,
    "语用适当性": 0.3,
    "逻辑一致性": 0.1,
    "招聘专业度": 0.2,
}


@dataclass
class FilePromptLoader:
    """从文件系统加载 prompt 文件。"""

    domain: str

    def load(self, name: str) -> str:
        """按名称加载单个 prompt 文本。

        Args:
            name: prompt 名称，对应 PROMPT_MAP 的键（如 "aggregator", "verifier"）
        """
        filename = PROMPT_MAP.get(name)
        if not filename:
            raise KeyError(f"Unknown prompt name: {name}")
        if isinstance(filename, dict):
            raise KeyError(f"Use load_explorers() for multi-perspective prompts, not load('{name}')")

        return self._read_file(filename)

    def load_explorers(self) -> list[tuple[str, str, float]]:
        """加载所有 Phase 2 多视角 prompt，返回 [(名称, prompt, temperature), ...] 列表。"""
        explorer_files = PROMPT_MAP.get("explorers")
        if not explorer_files or not isinstance(explorer_files, dict):
            raise KeyError("PROMPT_MAP['explorers'] not found or invalid")

        result = []
        for name, filename in explorer_files.items():
            prompt_text = self._read_file(filename)
            temperature = EXPLORER_TEMPERATURES.get(name, 0.3)
            result.append((name, prompt_text, temperature))
        return result

    def _read_file(self, filename: str) -> str:
        """读取 prompt 文件内容。"""
        base = Path(__file__).parent.parent
        prompt_path = base / "domains" / self.domain / "prompts" / filename
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
        return prompt_path.read_text(encoding="utf-8")

    def to_domain_prompts(self) -> DomainPrompts:
        """将所有 prompt 加载为 DomainPrompts 对象。"""
        return DomainPrompts(
            explorers=self.load_explorers(),
            topic_clustering=self.load("topic_clustering"),
            merge_clusters=self.load("merge_clusters"),
            aggregator=self.load("aggregator"),
            verifier=self.load("verifier"),
        )