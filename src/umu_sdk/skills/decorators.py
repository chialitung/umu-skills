"""Skill 装饰器与执行上下文.

提供 `@skill()` 装饰器用于声明高阶 Skill，以及 Skill 执行时使用的上下文。
"""

from __future__ import annotations

import inspect
import logging
import typing
from dataclasses import dataclass, field
from typing import Any, Callable

from .mcp_client import MCPClientManager
from .models import SkillInfo, SkillParameter, SkillFunction


@dataclass
class SkillContext:
    """Skill 执行上下文.

    每个 Skill 在执行时都会收到一个上下文实例，用于访问子 MCP、日志等能力。
    """

    mcp: MCPClientManager
    skill_name: str = ""
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("umu.mcp.skills"))

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """便捷方法：调用子 MCP 工具并返回解析后的 dict.

        返回值包含 success/data/error_code/error_message 等字段。
        """
        result = await self.mcp.call_tool(
            server=server,
            tool=tool,
            arguments=arguments,
            read_timeout_seconds=read_timeout_seconds,
        )
        return {
            "success": result.success,
            "data": result.data,
            "error_code": result.error_code,
            "error_message": result.error_message,
        }


# 类型别名：Skill 函数签名接受 SkillContext 作为第一个位置参数
SkillCallable = Callable[..., Any]


def _python_type_to_json_type(annotation: Any) -> str:
    """将 Python 类型注解粗略映射为 JSON Schema 类型名."""
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        if origin is list or annotation is list:
            return "array"
        if origin is dict or annotation is dict:
            return "object"
        return "string"

    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
        Any: "string",
    }
    return type_map.get(annotation, "string")


def _build_skill_info(
    func: SkillCallable,
    name: str,
    description: str,
    required_servers: list[str],
    return_description: str,
) -> SkillInfo:
    """根据函数签名构建 SkillInfo."""
    sig = inspect.signature(func)
    try:
        hints = typing.get_type_hints(func)
    except Exception:
        hints = {}

    parameters: list[SkillParameter] = []

    for param_name, param in sig.parameters.items():
        # 第一个参数约定为 SkillContext，不暴露为 Skill 参数
        if param_name == "ctx":
            continue

        annotation = hints.get(param_name, param.annotation)
        param_type = _python_type_to_json_type(annotation)
        required = param.default is inspect.Parameter.empty
        default = None if required else param.default

        parameters.append(
            SkillParameter(
                name=param_name,
                description="",
                type=param_type,
                required=required,
                default=default,
            )
        )

    return SkillInfo(
        name=name,
        description=description,
        required_servers=required_servers,
        parameters=parameters,
        return_description=return_description,
    )


def skill(
    name: str,
    description: str,
    required_servers: list[str] | None = None,
    return_description: str = "",
) -> Callable[[SkillCallable], SkillFunction]:
    """声明一个高阶 Skill.

    被装饰的函数第一个参数必须是 `ctx: SkillContext`，其余参数作为 Skill 的输入。

    示例：
        @skill(
            name="create_course_with_scorm",
            description="创建空课程并添加 SCORM 小节",
            required_servers=["teacher"],
        )
        async def create_course_with_scorm(
            ctx: SkillContext,
            title: str,
            scorm_path: str,
        ) -> str:
            ...
    """

    def decorator(func: SkillCallable) -> SkillFunction:
        info = _build_skill_info(
            func=func,
            name=name,
            description=description,
            required_servers=list(required_servers or []),
            return_description=return_description,
        )

        wrapped = SkillFunction(func=func, info=info)
        # 保留原始函数引用，便于 registry 通过属性识别
        func._umu_skill_function = wrapped
        return wrapped

    return decorator


def is_skill_function(obj: Any) -> bool:
    """判断对象是否是被 @skill 装饰的函数."""
    return isinstance(obj, SkillFunction) or hasattr(obj, "_umu_skill_function")


def get_skill_function(obj: Any) -> SkillFunction | None:
    """从对象中提取 SkillFunction."""
    if isinstance(obj, SkillFunction):
        return obj
    if hasattr(obj, "_umu_skill_function"):
        return obj._umu_skill_function
    return None


__all__ = [
    "SkillContext",
    "SkillCallable",
    "skill",
    "is_skill_function",
    "get_skill_function",
]
