# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Skills orchestration layer models.

定义技能编排层使用的 Pydantic 模型，包括子 MCP 服务器配置、
Skill 元数据、执行请求/结果等。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    """子 MCP 服务器配置.

    用于描述一个可由编排层连接并调度的子 MCP Server。
    """

    name: str = Field(..., description="服务器逻辑名称，如 teacher/student/admin")
    command: str = Field(..., description="启动子 MCP 的可执行命令")
    args: list[str] = Field(default_factory=list, description="传递给命令的额外参数")
    env: dict[str, str] | None = Field(
        default=None,
        description="子进程环境变量；None 表示继承当前进程环境",
    )
    enabled: bool = Field(default=True, description="是否随 orchestrator 自动启动")


class SkillsConfig(BaseModel):
    """Skills 编排层全局配置."""

    servers: list[ServerConfig] = Field(
        default_factory=list,
        description="子 MCP 服务器列表",
    )
    read_timeout_seconds: float | None = Field(
        default=60.0,
        description="调用子 MCP 工具时的默认超时（秒）",
    )

    @classmethod
    def default(cls) -> SkillsConfig:
        """返回包含现有 3 个 MCP 的默认配置."""
        return cls(
            servers=[
                ServerConfig(name="teacher", command="umu-skills-teacher"),
                ServerConfig(name="student", command="umu-skills-student"),
                ServerConfig(name="admin", command="umu-skills-admin"),
            ],
        )


class SkillParameter(BaseModel):
    """Skill 输入参数描述."""

    name: str
    description: str = ""
    type: str = "string"
    required: bool = True
    default: Any | None = None


class SkillInfo(BaseModel):
    """Skill 元数据."""

    name: str
    description: str
    required_servers: list[str] = Field(default_factory=list)
    parameters: list[SkillParameter] = Field(default_factory=list)
    return_description: str = ""


class PartialResult(BaseModel):
    """Skill 执行过程中某一步的中间结果."""

    step: int
    server: str
    tool: str
    success: bool
    data: Any = None
    error_code: str = ""
    error_message: str = ""


class SkillRunResult(BaseModel):
    """Skill 执行结果（与现有 MCP 工具返回信封对齐）."""

    success: bool
    data: Any = None
    error_code: str = ""
    error_message: str = ""
    suggested_action: str = ""
    next_action: str = "proceed"
    partial_results: list[PartialResult] = Field(default_factory=list)


@dataclass(frozen=True)
class SkillFunction:
    """被 @skill 装饰的函数及其元数据包装."""

    func: Any
    info: SkillInfo

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return await self.func(*args, **kwargs)


__all__ = [
    "ServerConfig",
    "SkillsConfig",
    "SkillParameter",
    "SkillInfo",
    "PartialResult",
    "SkillRunResult",
    "SkillFunction",
]
