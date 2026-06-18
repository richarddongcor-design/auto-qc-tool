"""配置加载器，从 YAML 文件读取所有可调参数。"""

from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None


@dataclass
class DataConfig:
    input_path: str = "data/input.xlsx"
    chunk_size: int = 0  # 0 = 自动计算
    domain: str = "recruitment"  # 领域名称，切换场景时修改此值


@dataclass
class LlmConfig:
    """LLM API 配置。空字段自动从 .env 文件读取，无需手动配置。"""
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    max_retries: int = 3
    retry_delay: int = 10                # 秒
    timeout: int = 300                   # 秒
    concurrency: int = 10                # 最大并发数

    def __post_init__(self):
        """config.yaml 中未填写的字段，从 .env 文件自动补充。"""
        from dotenv import load_dotenv
        import os
        load_dotenv()
        if not self.api_key:
            self.api_key = os.getenv("LLM_API_KEY", "")
        if not self.base_url:
            self.base_url = os.getenv("LLM_BASE_URL", "")
        if not self.model:
            self.model = os.getenv("LLM_MODEL", "")


@dataclass
class HarnessConfig:
    data: DataConfig = field(default_factory=DataConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)

    @classmethod
    def from_yaml(cls, path: str) -> "HarnessConfig":
        """从 YAML 文件加载配置，文件不存在时返回默认值。"""
        config_path = Path(path)
        if not config_path.exists() or yaml is None:
            return cls()
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if raw is None:  # 空文件或只有注释的 YAML 返回 None
            return cls()
        return cls._from_dict(raw)

    @classmethod
    def _from_dict(cls, raw: dict) -> "HarnessConfig":
        return cls(
            data=DataConfig(**raw.get("data", {})),
            llm=LlmConfig(**raw.get("llm", {})),
        )


def compute_chunk_size(total_dialogues: int) -> int:
    """根据对话总数自动计算 chunk_size。

    原则：
    - 控制 chunk 数量在 5-150 个之间
    - 小数据量用更小的 chunk 保证分析质量
    - 大数据量用更大的 chunk 控制 Agent 数量
    """
    if total_dialogues <= 0:
        return 100

    # 阶梯式自动匹配
    if total_dialogues <= 500:
        return 60        # 500 条 → ~8 chunks
    elif total_dialogues <= 2000:
        return 150       # 2000 条 → ~13 chunks
    elif total_dialogues <= 5000:
        return 200       # 5000 条 → 25 chunks
    elif total_dialogues <= 10000:
        return 250       # 1w 条 → 40 chunks
    elif total_dialogues <= 30000:
        return 300       # 3w 条 → 100 chunks
    else:
        return 500       # 5w+ 条 → chunks 控制在 100-150


def get_default_config_path() -> str:
    """返回默认配置文件路径（相对于 pi/agents/）。"""
    return str(Path(__file__).parent / "config.yaml")
