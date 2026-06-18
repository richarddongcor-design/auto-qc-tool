"""领域插件加载器。

从 config.yaml 读取 domain 字段，动态加载对应领域的插件。
加载失败时给出清晰的错误提示。
"""
import importlib
import logging
from pathlib import Path
from auto_qc.pi.domains.base import DomainPlugin

logger = logging.getLogger(__name__)


def load_domain(domain_name: str) -> DomainPlugin:
    """根据 domain 名称加载领域插件。

    加载路径: auto_qc.pi.domains/{domain_name}/plugin.py
    入口类: 该文件中的 Domain 类（必须继承 DomainPlugin）

    Raises:
        ImportError: 领域模块不存在
        ValueError: 领域模块中没有 Domain 类
    """
    module_path = f"auto_qc.pi.domains.{domain_name}.plugin"
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError:
        raise ImportError(
            f"找不到领域插件 '{domain_name}'。\n"
            f"请确保存在文件: auto_qc.pi/domains/{domain_name}/plugin.py\n"
            f"可用的领域: {list_available_domains()}"
        )

    if not hasattr(module, "Domain"):
        raise ValueError(
            f"领域 '{domain_name}' 的 plugin.py 中没有定义 Domain 类。\n"
            f"请确保有一个继承自 DomainPlugin 的 Domain 类。"
        )

    domain = module.Domain()
    if not isinstance(domain, DomainPlugin):
        raise ValueError(
            f"领域 '{domain_name}' 的 Domain 类没有继承自 DomainPlugin。\n"
            f"请检查 auto_qc.pi/domains/{domain_name}/plugin.py"
        )

    logger.info(f"已加载领域插件: {domain.name}")
    return domain


def list_available_domains() -> list[str]:
    """列出所有可用的领域。

    扫描 auto_qc.pi/domains/ 目录，找出所有包含 plugin.py 的子目录。
    """
    domains_dir = Path(__file__).parent.parent / "domains"
    if not domains_dir.is_dir():
        return []
    available = []
    for item in domains_dir.iterdir():
        if item.is_dir() and (item / "plugin.py").exists():
            available.append(item.name)
    return sorted(available)
