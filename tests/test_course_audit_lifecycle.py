"""课程审核生命周期 Skill 测试."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.helpers.skill_mock import SkillMockBuilder, err_response, ok_response
from umu_sdk.skills.builtin.course_audit_lifecycle import (
    validate_course_audit_lifecycle as lifecycle_skill,
)


@pytest.fixture
def mock_ctx() -> MagicMock:
    """构造 mock SkillContext."""
    ctx = MagicMock()
    ctx.call_tool = AsyncMock()
    return ctx


def _course_info(group_id: str, audit_status: int, access_code: str = "abc123") -> dict[str, Any]:
    return {
        "group_id": group_id,
        "title": "测试课程",
        "access_code": access_code,
        "audit_status": str(audit_status),
        "release_status": "2" if audit_status >= 0 else "0",
    }


def _add_initial_snapshot(
    builder: SkillMockBuilder,
    group_id: str,
    access_code: str,
) -> SkillMockBuilder:
    """追加初始快照响应."""
    return builder.add_snapshot(group_id, -1, access_code)


def _add_lifecycle_only(
    builder: SkillMockBuilder,
    group_id: str,
    access_code: str,
    umu_id: str,
) -> SkillMockBuilder:
    """追加单门课程生命周期中的修改与断言调用（不含初始/最终快照）.

    调用顺序（与 Skill 内部严格对应）：
    1. 提交：tch_submit_course_for_audit
    2. 提交后断言+快照：tch_get_course
    3. 查找 owner：adm_list_course_audit_records
    4. 通过：adm_audit_course
    5. 断言通过：tch_get_course
    6. 撤销：adm_audit_course
    7. 断言撤销：tch_get_course
    8. 再次提交：tch_submit_course_for_audit
    9. 断言再次提交：tch_get_course
    10. 拒绝+黑名单：adm_audit_course
    11. 断言拒绝：tch_get_course
    12. 移出黑名单：adm_save_course_blacklist
    13. 移除后二次确认黑名单：adm_list_course_blacklist
    14. 恢复阶段检查黑名单：adm_list_course_blacklist
    15. 恢复阶段检查状态：tch_get_course
    16. 恢复阶段撤销：adm_audit_course
    17. 恢复阶段断言：tch_get_course
    """
    builder.add_submit(group_id, audit_status=0)
    builder.add_course_info(group_id, 0, access_code)
    builder.add_audit_records(access_code, umu_id)
    builder.add_audit(group_id, "approve", audit_status=1)
    builder.add_course_info(group_id, 1, access_code)
    builder.add_audit(group_id, "revoke", audit_status=3)
    builder.add_course_info(group_id, 3, access_code)
    builder.add_submit(group_id, audit_status=0)
    builder.add_course_info(group_id, 0, access_code)
    builder.add_audit(group_id, "reject", audit_status=2, add_to_blacklist=True)
    builder.add_course_info(group_id, 2, access_code)
    builder.add_blacklist_save(umu_id, "remove")
    builder.add_blacklist_list()  # 移除后二次确认
    builder.add_blacklist_list()  # 恢复阶段检查
    builder.add_course_info(group_id, 2, access_code)
    builder.add_audit(group_id, "revoke", audit_status=3)
    builder.add_course_info(group_id, 3, access_code)
    return builder


def _add_final_snapshot(
    builder: SkillMockBuilder,
    group_id: str,
    access_code: str,
    final_status: int = 3,
) -> SkillMockBuilder:
    """追加最终快照响应."""
    return builder.add_final_snapshot(group_id, final_status, access_code)


def _build_success_lifecycle(
    builder: SkillMockBuilder,
    group_id: str,
    access_code: str,
    umu_id: str,
    final_status: int = 3,
) -> SkillMockBuilder:
    """按 Skill 调用顺序追加单门课程完整生命周期的 mock 响应.

    包含初始快照、生命周期、最终快照。单课程测试可直接使用；
    多课程测试应分别调用 _add_initial_snapshot、_add_lifecycle_only、_add_final_snapshot。
    """
    _add_initial_snapshot(builder, group_id, access_code)
    _add_lifecycle_only(builder, group_id, access_code, umu_id)
    _add_final_snapshot(builder, group_id, access_code, final_status)
    return builder


def _tool_calls(ctx: MagicMock) -> list[tuple[str, str]]:
    """提取 mock  ctx 的 (server, tool) 调用序列."""
    return [(c[0], c[1]) for c in ctx.calls]


@pytest.mark.asyncio
async def test_lifecycle_success_single_course(mock_ctx: MagicMock) -> None:
    """测试单门课程完整生命周期成功."""
    builder = _build_success_lifecycle(SkillMockBuilder(), "g-001", "code1", "u-001")
    ctx = builder.build()
    mock_ctx.call_tool = ctx.call_tool
    mock_ctx.calls = ctx.calls

    result = await lifecycle_skill(
        ctx=mock_ctx,
        group_ids=["g-001"],
    )

    assert result["success"] is True
    assert result["data"]["consistent"] is True
    assert result["data"]["completed_groups"] == ["g-001"]
    assert result["data"]["failed_groups"] == []

    expected_tools = [
        ("teacher", "tch_get_course"),
        ("teacher", "tch_submit_course_for_audit"),
        ("teacher", "tch_get_course"),
        ("admin", "adm_list_course_audit_records"),
        ("admin", "adm_audit_course"),
        ("teacher", "tch_get_course"),
        ("admin", "adm_audit_course"),
        ("teacher", "tch_get_course"),
        ("teacher", "tch_submit_course_for_audit"),
        ("teacher", "tch_get_course"),
        ("admin", "adm_audit_course"),
        ("teacher", "tch_get_course"),
        ("admin", "adm_save_course_blacklist"),
        ("admin", "adm_list_course_blacklist"),
        ("admin", "adm_list_course_blacklist"),
        ("teacher", "tch_get_course"),
        ("admin", "adm_audit_course"),
        ("teacher", "tch_get_course"),
        ("teacher", "tch_get_course"),
    ]
    assert _tool_calls(mock_ctx) == expected_tools


@pytest.mark.asyncio
async def test_lifecycle_success_multiple_courses(mock_ctx: MagicMock) -> None:
    """测试多门课程完整生命周期成功."""
    builder = SkillMockBuilder()
    configs = [
        {"group_id": "g-001", "access_code": "code1", "umu_id": "u-001"},
        {"group_id": "g-002", "access_code": "code2", "umu_id": "u-002"},
    ]

    # Skill 先采集所有初始快照
    for cfg in configs:
        _add_initial_snapshot(builder, cfg["group_id"], cfg["access_code"])

    # 再执行所有课程的生命周期
    for cfg in configs:
        _add_lifecycle_only(
            builder,
            cfg["group_id"],
            cfg["access_code"],
            cfg["umu_id"],
        )

    # 最后采集所有最终快照
    for cfg in configs:
        _add_final_snapshot(builder, cfg["group_id"], cfg["access_code"])

    ctx = builder.build()
    mock_ctx.call_tool = ctx.call_tool
    mock_ctx.calls = ctx.calls

    result = await lifecycle_skill(
        ctx=mock_ctx,
        group_ids=["g-001", "g-002"],
    )

    assert result["success"] is True
    assert result["data"]["consistent"] is True
    assert set(result["data"]["completed_groups"]) == {"g-001", "g-002"}
    assert result["data"]["failed_groups"] == []


@pytest.mark.asyncio
async def test_lifecycle_dry_run(mock_ctx: MagicMock) -> None:
    """测试 dry_run 模式只返回快照与计划."""
    builder = SkillMockBuilder()
    builder.add_snapshot("g-001", -1, "code1")
    ctx = builder.build()
    mock_ctx.call_tool = ctx.call_tool
    mock_ctx.calls = ctx.calls

    result = await lifecycle_skill(
        ctx=mock_ctx,
        group_ids=["g-001"],
        dry_run=True,
    )

    assert result["success"] is True
    assert result["data"]["dry_run"] is True
    assert result["data"]["consistent"] is True
    assert result["data"]["planned_steps"]
    assert result["data"]["estimated_calls"] > 0

    # dry_run 只应调用一次 tch_get_course
    assert _tool_calls(mock_ctx) == [("teacher", "tch_get_course")]


@pytest.mark.asyncio
async def test_lifecycle_empty_group_ids(mock_ctx: MagicMock) -> None:
    """测试空 group_ids 且无 auto_select 时返回错误."""
    result = await lifecycle_skill(
        ctx=mock_ctx,
        group_ids=[],
    )

    assert result["success"] is False
    assert result["error_code"] == "EMPTY_GROUP_IDS"


@pytest.mark.asyncio
async def test_lifecycle_auto_select_unsubmitted(mock_ctx: MagicMock) -> None:
    """测试 auto_select_unsubmitted 自动筛选未提交课程."""
    builder = SkillMockBuilder()
    # 第一页课程列表：2 门课程
    builder.add_created_courses(
        [
            {"group_id": "g-001", "title": "课程 A"},
            {"group_id": "g-002", "title": "课程 B"},
        ]
    )
    # 查询每门课程状态：g-001 未提交，g-002 已提交
    builder.add_course_info("g-001", -1, "code1")
    builder.add_course_info("g-002", 1, "code2")
    # 对 g-001 执行完整生命周期
    _build_success_lifecycle(builder, "g-001", "code1", "u-001")

    ctx = builder.build()
    mock_ctx.call_tool = ctx.call_tool
    mock_ctx.calls = ctx.calls

    result = await lifecycle_skill(
        ctx=mock_ctx,
        auto_select_unsubmitted=True,
        max_auto_select=10,
    )

    assert result["success"] is True
    assert result["data"]["completed_groups"] == ["g-001"]
    assert result["data"]["failed_groups"] == []

    # 验证查询过 g-001 与 g-002 的状态
    tool_calls = _tool_calls(mock_ctx)
    assert tool_calls.count(("teacher", "tch_get_course")) >= 3  # 2 次筛选 + 至少 1 次生命周期


@pytest.mark.asyncio
async def test_lifecycle_inconsistent_final_state(mock_ctx: MagicMock) -> None:
    """测试最终状态不一致时返回 consistent=false."""
    builder = SkillMockBuilder()
    _build_success_lifecycle(builder, "g-001", "code1", "u-001", final_status=2)

    ctx = builder.build()
    mock_ctx.call_tool = ctx.call_tool
    mock_ctx.calls = ctx.calls

    result = await lifecycle_skill(
        ctx=mock_ctx,
        group_ids=["g-001"],
    )

    assert result["success"] is False
    assert result["data"]["consistent"] is False
    assert result["data"]["final_snapshot"][0]["audit_status"] == 2


# 失败点与对应响应队列中的索引（基于 _build_success_lifecycle 的顺序，0-based）
_FAIL_INDEX = {
    "submit": 1,
    "approve": 4,
    "revoke_after_approve": 6,
    "resubmit": 8,
    "reject_with_blacklist": 10,
    "remove_blacklist": 12,
    "final_restore": 16,
}


@pytest.mark.parametrize("fail_at", list(_FAIL_INDEX.keys()))
@pytest.mark.asyncio
async def test_lifecycle_failure_at_each_stage(fail_at: str, mock_ctx: MagicMock) -> None:
    """测试每个主要阶段失败时都能被正确捕获并报告."""
    builder = SkillMockBuilder()
    _build_success_lifecycle(builder, "g-001", "code1", "u-001")

    # 在指定位置注入失败响应
    fail_idx = _FAIL_INDEX[fail_at]
    builder.inject_response(fail_idx, err_response("STAGE_FAILED", f"{fail_at} 失败"))

    # 追加充足的兜底响应，供可能的恢复逻辑使用
    for _ in range(20):
        builder.add_response(ok_response({}))

    ctx = builder.build()
    mock_ctx.call_tool = ctx.call_tool
    mock_ctx.calls = ctx.calls

    result = await lifecycle_skill(
        ctx=mock_ctx,
        group_ids=["g-001"],
    )

    assert result["success"] is False
    assert result["data"]["consistent"] is False
    assert len(result["data"]["failed_groups"]) == 1
    assert result["data"]["failed_groups"][0]["stage"] == fail_at

    # 对会产生状态变更的阶段，验证恢复调用存在
    if fail_at in ("approve", "reject_with_blacklist", "remove_blacklist", "final_restore"):
        tool_calls = _tool_calls(mock_ctx)
        assert ("admin", "adm_audit_course") in tool_calls
