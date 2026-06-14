"""生命周期测试 Skill 的公共辅助函数.

提供跨生命周期 Skill 复用的工具：进度输出、状态查询、状态断言、
通用错误类型与阶段执行模板。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .decorators import SkillContext


def report_progress(skill_name: str, message: str) -> None:
    """向 stderr 输出生命周期执行进度.

    MCP server 通过 stdio 通信，进度信息必须写到 stderr，避免破坏 JSON-RPC。
    """
    print(f"[{skill_name}] {message}", file=sys.stderr)


class LifecycleError(Exception):
    """生命周期测试内部异常."""

    def __init__(self, message: str, stage: str = "") -> None:
        super().__init__(message)
        self.stage = stage


def _normalize_audit_status(status: Any) -> int:
    """将各种形式的 audit_status 归一化为 int，缺省 -1."""
    if status is None:
        return -1
    if isinstance(status, int):
        return status
    if isinstance(status, str):
        try:
            return int(status)
        except ValueError:
            return -1
    return -1


async def get_course_audit_status(
    ctx: SkillContext,
    group_id: str,
) -> int:
    """查询课程当前审核状态.

    Args:
        ctx: Skill 上下文。
        group_id: 课程 ID。

    Returns:
        审核状态码，缺省 -1。

    Raises:
        LifecycleError: 查询失败时抛出。
    """
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_get_course",
        arguments={"group_id": group_id, "include_fulltext": False},
    )
    if not result.get("success"):
        raise LifecycleError(
            f"查询课程 {group_id} 审核状态失败: {result.get('error_message')}",
            stage="status_query",
        )
    course_info = result.get("data") or {}
    return _normalize_audit_status(course_info.get("audit_status"))


async def assert_audit_status(
    ctx: SkillContext,
    group_id: str,
    expected: int,
    stage: str,
) -> None:
    """断言课程审核状态与预期一致.

    在状态变更操作后调用，可及早发现 API 返回成功但状态未实际变更的异常。
    """
    actual = await get_course_audit_status(ctx, group_id)
    if actual != expected:
        raise LifecycleError(
            f"阶段 {stage}: 课程 {group_id} 期望 audit_status={expected}, 实际={actual}",
            stage=stage,
        )


async def get_course_snapshot(
    ctx: SkillContext,
    group_id: str,
) -> dict[str, Any]:
    """获取课程当前状态快照.

    返回包含 audit_status、title、access_code 的字典；
    若获取失败则抛出 LifecycleError。
    """
    info_result = await ctx.call_tool(
        server="teacher",
        tool="tch_get_course",
        arguments={"group_id": group_id, "include_fulltext": False},
    )
    if not info_result.get("success"):
        raise LifecycleError(
            f"获取课程 {group_id} 信息失败: {info_result.get('error_message')}",
            stage="snapshot",
        )

    course_info = info_result.get("data") or {}
    return {
        "group_id": group_id,
        "title": course_info.get("title", ""),
        "access_code": course_info.get("access_code", ""),
        "audit_status": _normalize_audit_status(course_info.get("audit_status")),
        "release_status": course_info.get("release_status", "0"),
    }


@dataclass
class LifecycleStage:
    """单个生命周期阶段定义."""

    name: str
    server: str
    tool: str
    # 支持静态参数或同步/异步工厂函数，便于需要动态参数的阶段
    arguments: dict[str, Any] | Callable[[], dict[str, Any]] | Callable[[], Awaitable[dict[str, Any]]]
    expected_status: int | None = None
    description: str = ""
    # 阶段执行前/后的钩子，用于处理跨阶段依赖（如获取 owner_umu_id）
    before: Callable[[], Awaitable[None]] | None = None
    after: Callable[[], Awaitable[None]] | None = None


async def _resolve_arguments(
    arguments: dict[str, Any] | Callable[[], dict[str, Any]] | Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """解析阶段参数（支持静态 dict 或同步/异步工厂函数）."""
    if callable(arguments):
        result = arguments()
        if hasattr(result, "__await__"):
            return await result
        return result
    return arguments


async def run_lifecycle_stages(
    ctx: SkillContext,
    skill_name: str,
    group_id: str,
    stages: list[LifecycleStage],
    timeline: list[dict[str, Any]],
) -> None:
    """按顺序执行生命周期阶段，自动记录时间线并校验状态.

    任一阶段失败会抛出 LifecycleError，由调用方决定是否触发恢复。
    """
    for stage in stages:
        desc = stage.description or stage.name
        report_progress(skill_name, f"课程 {group_id} 正在执行：{desc}")

        if stage.before:
            await stage.before()

        args = await _resolve_arguments(stage.arguments)
        result = await ctx.call_tool(
            server=stage.server,
            tool=stage.tool,
            arguments=args,
        )

        timeline.append({
            "stage": stage.name,
            "group_id": group_id,
            "success": result.get("success", False),
            "error": result.get("error_message", ""),
            "data": result.get("data"),
        })

        if not result.get("success"):
            raise LifecycleError(
                f"{stage.name} 失败: {result.get('error_message')}",
                stage=stage.name,
            )

        if stage.expected_status is not None:
            await assert_audit_status(ctx, group_id, stage.expected_status, stage.name)

        if stage.after:
            await stage.after()

        report_progress(skill_name, f"课程 {group_id} {desc} 完成")


__all__ = [
    "LifecycleError",
    "LifecycleStage",
    "assert_audit_status",
    "get_course_audit_status",
    "get_course_snapshot",
    "report_progress",
    "run_lifecycle_stages",
]
