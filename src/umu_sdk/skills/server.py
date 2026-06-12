"""UMU 统一技能编排 MCP Server.

将多个子 MCP（teacher/student/admin）封装为高阶 Skill，
对外暴露 skill_list / skill_describe / skill_run 三个核心工具。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp.server.fastmcp import FastMCP

from .config import get_config
from .decorators import SkillContext
from .mcp_client import MCPClientManager
from .registry import SkillRegistry

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------


def _setup_logging() -> None:
    """配置结构化日志."""
    level_name = os.getenv("MCP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    fmt = os.getenv(
        "MCP_LOG_FORMAT",
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    handler.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger("umu.mcp.skills")
    root.setLevel(level)
    root.handlers = [handler]


_setup_logging()
logger = logging.getLogger("umu.mcp.skills")

# ---------------------------------------------------------------------------
# 全局实例（由 lifespan 管理）
# ---------------------------------------------------------------------------
_mcp_client: MCPClientManager | None = None
_skill_registry: SkillRegistry | None = None


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """应用生命周期管理.

    启动时：
    1. 加载 Skills 配置；
    2. 启动所有启用的子 MCP 连接；
    3. 加载内置 Skill 并校验所需服务器是否可用。
    关闭时释放所有子进程资源。
    """
    global _mcp_client, _skill_registry

    base_url = os.getenv("UMU_BASE_URL", "https://www.umu.cn")
    logger.info("UMU Skills Orchestrator 启动中，目标: %s", base_url)

    # 加载配置
    config = get_config()
    logger.info("已加载 %d 个子 MCP 配置", len(config.servers))

    # 启动子 MCP 连接
    _mcp_client = MCPClientManager(config.servers)
    try:
        await _mcp_client.start()
    except Exception as e:
        logger.error("启动子 MCP 连接失败: %s", e)
        _mcp_client = None
        raise

    available_servers = _mcp_client.list_servers()
    logger.info("已连接子 MCP: %s", ", ".join(available_servers) or "无")

    # 加载 Skill
    _skill_registry = SkillRegistry()
    _skill_registry.load_builtin_skills()

    missing = _skill_registry.validate_servers(available_servers)
    if missing:
        logger.warning(
            "以下 Skill 所需子 MCP 未连接: %s",
            ", ".join(missing),
        )

    logger.info("UMU Skills Orchestrator 已启动，加载 %d 个 Skill", len(_skill_registry.list_skills()))

    yield {
        "mcp_client": _mcp_client,
        "skill_registry": _skill_registry,
    }

    # 清理
    if _mcp_client:
        await _mcp_client.stop()
        _mcp_client = None
    _skill_registry = None
    logger.info("UMU Skills Orchestrator 已关闭")


# ---------------------------------------------------------------------------
# 创建 MCP 服务器
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "umu-skills",
    instructions="""UMU 学习平台统一技能编排 MCP 服务。

将 Teacher / Student / Admin 三个子 MCP 的原子工具封装为高阶 Skill，
让 AI 只需调用一个 Skill 即可完成跨角色的复杂流程。

核心工具：
- skill_list：列出所有可用 Skill
- skill_describe：查看指定 Skill 的输入参数说明
- skill_run：执行指定 Skill
- skill_call_atomic_tool：直接调用子 MCP 的任意原子工具（兜底/探索场景）

内置示例 Skill：
- create_course_with_scorm：创建空课程并添加 SCORM 小节
- enroll_course：学员报名课程
- get_course_progress：查询学员课程进度
- batch_onboard_users：批量创建学员账号并报名课程

AI 使用本服务时，可直接描述目标流程，由 orchestrator 自动选择并执行 Skill。
""",
    lifespan=app_lifespan,
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def _ok(
    data: Any = None,
    next_action: str = "proceed",
    suggested_action: str = "",
    **kwargs: Any,
) -> str:
    """构造成功返回结构."""
    result: dict[str, Any] = {
        "success": True,
        "data": data,
        "error_code": "",
        "error_message": "",
        "suggested_action": suggested_action,
        "next_action": next_action,
    }
    result.update(kwargs)
    return json.dumps(result, ensure_ascii=False, default=str)


def _err(
    error_code: str,
    error_message: str,
    suggested_action: str = "",
    data: Any = None,
    **kwargs: Any,
) -> str:
    """构造失败返回结构."""
    result: dict[str, Any] = {
        "success": False,
        "data": data,
        "error_code": error_code,
        "error_message": error_message,
        "suggested_action": suggested_action,
        "next_action": "retry",
    }
    result.update(kwargs)
    return json.dumps(result, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool()
async def skill_list() -> str:
    """列出所有可用 Skill 的元数据."""
    if _skill_registry is None:
        return _err("REGISTRY_NOT_READY", "Skill 注册表未初始化")

    skills = _skill_registry.list_skills()
    return _ok(
        data=[info.model_dump(exclude_none=True) for info in skills],
        next_action="proceed",
    )


@mcp.tool()
async def skill_describe(name: str) -> str:
    """查看指定 Skill 的详细说明和输入参数.

    Args:
        name: Skill 名称。
    """
    if _skill_registry is None:
        return _err("REGISTRY_NOT_READY", "Skill 注册表未初始化")

    try:
        skill = _skill_registry.get_skill(name)
    except KeyError as e:
        return _err("SKILL_NOT_FOUND", str(e))

    info = skill.info
    return _ok(
        data={
            "name": info.name,
            "description": info.description,
            "required_servers": info.required_servers,
            "parameters": [p.model_dump(exclude_none=True) for p in info.parameters],
            "return_description": info.return_description,
        },
        next_action="proceed",
    )


@mcp.tool()
async def skill_run(name: str, arguments: dict[str, Any]) -> str:
    """执行指定 Skill.

    Args:
        name: Skill 名称。
        arguments: Skill 输入参数字典。
    """
    if _mcp_client is None or _skill_registry is None:
        return _err("SERVER_NOT_READY", "Orchestrator 尚未完成初始化")

    try:
        skill = _skill_registry.get_skill(name)
    except KeyError as e:
        return _err("SKILL_NOT_FOUND", str(e))

    # 校验所需子 MCP 是否可用
    available = set(_mcp_client.list_servers())
    missing = [s for s in skill.info.required_servers if s not in available]
    if missing:
        return _err(
            "SERVER_UNAVAILABLE",
            f"Skill [{name}] 所需子 MCP 未连接: {', '.join(missing)}",
            suggested_action="请检查 orchestrator 配置或启动对应的子 MCP",
        )

    ctx = SkillContext(
        mcp=_mcp_client,
        skill_name=name,
        logger=logger,
    )

    try:
        result = await skill.func(ctx, **arguments)
    except TypeError as e:
        return _err(
            "INVALID_ARGUMENTS",
            f"参数不匹配: {e}",
            suggested_action=f"请调用 skill_describe(name='{name}') 查看参数说明",
            next_action="needs_user_input",
        )
    except Exception as e:
        logger.exception("执行 Skill [%s] 时出错", name)
        return _err(
            "SKILL_EXECUTION_ERROR",
            f"执行 Skill 时发生异常: {e}",
            suggested_action="请检查子 MCP 日志或调整输入参数",
        )

    # 统一序列化：函数返回 dict 则按标准信封输出；返回 str 则直接透传
    if isinstance(result, dict):
        # 如果函数已经返回标准信封，则直接序列化；否则包装一层
        if "success" in result:
            return json.dumps(result, ensure_ascii=False, default=str)
        return _ok(data=result)

    if isinstance(result, str):
        return result

    return _ok(data=result)


@mcp.tool()
async def skill_call_atomic_tool(
    server: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
    read_timeout_seconds: float | None = None,
) -> str:
    """直接调用子 MCP 的任意原子工具（兜底/探索场景）。

    仅用于尚未封装为 Skill 的低频工具或新增工具。AI 应优先使用 skill_run。

    Args:
        server: 子 MCP 名称（teacher/student/admin）。
        tool: 原子工具名称（如 tch_get_categories）。
        arguments: 透传给原子工具的参数字典。
        read_timeout_seconds: 可选超时秒数。
    """
    if _mcp_client is None:
        return _err("SERVER_NOT_READY", "Orchestrator 尚未完成初始化")

    available = set(_mcp_client.list_servers())
    if server not in available:
        return _err(
            "SERVER_UNAVAILABLE",
            f"子 MCP [{server}] 未连接。可用服务器: {', '.join(sorted(available)) or '无'}",
            suggested_action="请检查 orchestrator 配置或启动对应的子 MCP",
        )

    try:
        result = await _mcp_client.call_tool(
            server=server,
            tool=tool,
            arguments=arguments or {},
            read_timeout_seconds=read_timeout_seconds,
        )
    except Exception as e:
        logger.exception("透传调用原子工具 [%s/%s] 时出错", server, tool)
        return _err(
            "ATOMIC_TOOL_ERROR",
            f"调用原子工具时发生异常: {e}",
            suggested_action="请检查子 MCP 日志或工具参数",
        )

    if not result.success:
        return _err(
            error_code=result.error_code or "ATOMIC_TOOL_FAILED",
            error_message=result.error_message or "原子工具调用失败",
            data=result.data,
        )

    return _ok(data=result.data)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main() -> None:
    """CLI 入口."""
    mcp.run()


__all__ = ["mcp", "main", "app_lifespan"]
