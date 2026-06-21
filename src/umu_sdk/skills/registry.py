# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Skill 注册表.

负责发现、注册、查询 Skill，并支持从模块自动加载被 @skill 装饰的函数。
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import Any

from .decorators import SkillFunction, get_skill_function, is_skill_function
from .models import SkillInfo

logger = logging.getLogger("umu.mcp.skills")


class SkillRegistry:
    """Skill 注册表.

    维护 name -> SkillFunction 的映射，提供注册、发现、查询能力。
    """

    def __init__(self) -> None:
        self._skills: dict[str, SkillFunction] = {}

    def register(self, skill: SkillFunction) -> None:
        """注册一个 Skill."""
        name = skill.info.name
        if name in self._skills:
            logger.warning("Skill [%s] 已存在，将被覆盖", name)
        self._skills[name] = skill
        logger.debug("Skill [%s] 已注册", name)

    def register_function(self, func: Any) -> None:
        """从任意对象（通常是被 @skill 装饰的函数）中提取并注册 Skill."""
        sf = get_skill_function(func)
        if sf is None:
            raise ValueError(f"对象 {func!r} 不是被 @skill 装饰的函数")
        self.register(sf)

    def register_from_module(self, module: Any) -> None:
        """扫描模块中的所有被 @skill 装饰的函数并注册."""
        count = 0
        for _name, obj in inspect.getmembers(module):
            if is_skill_function(obj):
                self.register_function(obj)
                count += 1
        logger.info("从模块 %s 注册了 %d 个 Skill", getattr(module, "__name__", "?"), count)

    def load_builtin_skills(self, package_name: str = "umu_sdk.skills.builtin") -> None:
        """自动加载内置 Skill 包中的所有模块."""
        self.load_skill_package(package_name)

    def load_skill_package(self, package_name: str) -> None:
        """自动加载指定 Skill 包中的所有模块."""
        try:
            package = importlib.import_module(package_name)
        except ImportError as e:
            logger.warning("加载 Skill 包失败: %s", e)
            return

        prefix = package.__name__ + "."
        for _finder, mod_name, is_pkg in pkgutil.iter_modules(
            package.__path__ or [], prefix=prefix
        ):
            if is_pkg:
                continue
            try:
                module = importlib.import_module(mod_name)
                self.register_from_module(module)
            except Exception as e:
                logger.warning("加载 Skill 模块 %s 失败: %s", mod_name, e)

    def list_skills(self) -> list[SkillInfo]:
        """返回所有已注册 Skill 的元数据."""
        return [sf.info for sf in self._skills.values()]

    def get_skill(self, name: str) -> SkillFunction:
        """按名称获取 Skill."""
        if name not in self._skills:
            available = ", ".join(sorted(self._skills.keys())) or "无"
            raise KeyError(f"Skill [{name}] 不存在。可用 Skill: {available}")
        return self._skills[name]

    def has_skill(self, name: str) -> bool:
        """判断是否存在指定 Skill."""
        return name in self._skills

    def validate_servers(self, available_servers: list[str]) -> list[str]:
        """校验所有 Skill 所需的子 MCP 是否都在可用列表中.

        返回缺失的服务器名称列表。
        """
        available = set(available_servers)
        missing: set[str] = set()
        for sf in self._skills.values():
            for server in sf.info.required_servers:
                if server not in available:
                    missing.add(server)
        return sorted(missing)


__all__ = ["SkillRegistry"]
