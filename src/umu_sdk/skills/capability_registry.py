# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""能力域注册表.

自动扫描 `tools/operations/` 中被 `@umu_operation` 装饰的业务函数，
建立 `capability -> operation -> roles` 的映射，供 Skill 层按能力域调用。
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import Any

from ..tools.decorators import get_operation_info, is_umu_operation

logger = logging.getLogger("umu.mcp.skills")


DEFAULT_CAPABILITY: str = "general"

# 模块名到能力域的默认映射；operation 也可通过 @umu_operation(capabilities=[...]) 显式声明
_MODULE_CAPABILITY_MAP: dict[str, str] = {
    "learning": "learning",
    "courses": "learning",
    "programs": "program_management",
    "access_permissions": "permission_management",
}


class CapabilityRegistry:
    """能力域注册表.

    维护以下映射：
    - capability -> {operation_name: roles}
    - operation_name -> (roles, capabilities)
    """

    _instance: CapabilityRegistry | None = None

    def __init__(self) -> None:
        self._capability_operations: dict[str, dict[str, list[str]]] = {}
        self._operation_meta: dict[str, dict[str, Any]] = {}
        self._loaded = False

    @classmethod
    def get(cls) -> CapabilityRegistry:
        """返回全局单例（懒加载）."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def load(self, package_name: str = "umu_sdk.tools.operations") -> None:
        """扫描指定包下所有模块，提取 operation 与 capability 映射."""
        if self._loaded:
            return

        try:
            package = importlib.import_module(package_name)
        except ImportError as e:
            logger.warning("加载 operations 包失败: %s", e)
            return

        prefix = package.__name__ + "."
        for _finder, mod_name, is_pkg in pkgutil.iter_modules(
            package.__path__ or [], prefix=prefix
        ):
            if is_pkg:
                continue
            try:
                module = importlib.import_module(mod_name)
            except Exception as e:
                logger.warning("加载 operation 模块 %s 失败: %s", mod_name, e)
                continue
            self._load_module(module)

        self._loaded = True
        logger.info(
            "CapabilityRegistry 加载完成: %d 个能力域, %d 个 operation",
            len(self._capability_operations),
            len(self._operation_meta),
        )

    def _load_module(self, module: Any) -> None:
        """扫描单个模块中的 @umu_operation."""
        module_name = getattr(module, "__name__", "")
        module_short = module_name.split(".")[-1] if module_name else ""
        default_capability = _MODULE_CAPABILITY_MAP.get(module_short, DEFAULT_CAPABILITY)

        for _name, obj in inspect.getmembers(module):
            if not is_umu_operation(obj):
                continue
            info = get_operation_info(obj)
            if info is None:
                continue

            op_name = info.name
            roles = list(info.roles)
            capabilities = list(info.capabilities) if info.capabilities else [default_capability]

            self._operation_meta[op_name] = {
                "roles": roles,
                "capabilities": capabilities,
                "description": info.description,
            }

            for capability in capabilities:
                self._capability_operations.setdefault(capability, {})[op_name] = roles

    def get_roles_for_operation(self, operation_name: str) -> list[str]:
        """返回指定 operation 支持的角色列表."""
        self.load()
        meta = self._operation_meta.get(operation_name)
        if meta is None:
            return []
        return list(meta["roles"])

    def get_operations_for_capability(self, capability: str) -> dict[str, list[str]]:
        """返回指定能力域下的所有 operation 及其支持角色."""
        self.load()
        return dict(self._capability_operations.get(capability, {}))

    def get_capabilities(self) -> list[str]:
        """返回所有已知能力域."""
        self.load()
        return sorted(self._capability_operations.keys())

    def operation_exists(self, operation_name: str) -> bool:
        """判断 operation 是否已注册."""
        self.load()
        return operation_name in self._operation_meta

    def get_operation_capabilities(self, operation_name: str) -> list[str]:
        """返回 operation 所属的能力域列表."""
        self.load()
        meta = self._operation_meta.get(operation_name)
        if meta is None:
            return []
        return list(meta["capabilities"])


def get_capability_registry() -> CapabilityRegistry:
    """便捷函数：返回全局能力域注册表."""
    return CapabilityRegistry.get()


__all__ = [
    "CapabilityRegistry",
    "get_capability_registry",
    "DEFAULT_CAPABILITY",
]
