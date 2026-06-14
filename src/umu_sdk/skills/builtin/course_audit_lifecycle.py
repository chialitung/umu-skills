"""课程审核生命周期测试 Skill.

本 Skill 用于证明 umu skill 框架在跨角色（Teacher + Admin）场景下的
有效性、准确性与使用便捷性：

- Teacher 提交课程审核；
- Admin 依次执行：通过、撤销、再次提交、拒绝并加入黑名单、移出黑名单；
- 最终自动恢复课程与讲师状态，返回完整状态变更时间线。
"""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill
from ..lifecycle_helpers import (
    LifecycleError,
    LifecycleStage,
    assert_audit_status,
    get_course_snapshot,
    report_progress,
    run_lifecycle_stages,
)


_SKILL_NAME = "validate_course_audit_lifecycle"


def _ok(
    data: Any = None,
    next_action: str = "proceed",
    suggested_action: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
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
    return result


def _err(
    error_code: str,
    error_message: str,
    suggested_action: str = "",
    data: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
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
    return result


async def _find_owner_umu_id(
    ctx: SkillContext,
    access_code: str,
) -> str | None:
    """通过访问码查询待审核记录，返回课程拥有者的 umu_id。

    课程提交审核后（audit_status=0）会进入审核记录列表，
    借此可获取创建者 umu_id，用于后续黑名单恢复。
    """
    if not access_code:
        return None

    records_result = await ctx.call_tool(
        server="admin",
        tool="adm_list_course_audit_records",
        arguments={
            "audit_status": 0,
            "access_code": access_code,
            "page": 1,
            "page_size": 20,
            "fetch_all": False,
        },
    )

    if not records_result.get("success"):
        return None

    records = (records_result.get("data") or {}).get("records", [])
    for record in records:
        if str(record.get("access_code", "")) == access_code:
            return record.get("umu_id") or record.get("teacher_id")

    return None


async def _is_user_blacklisted(
    ctx: SkillContext,
    umu_id: str,
) -> bool:
    """查询指定 umu_id 是否在课程提交黑名单中."""
    if not umu_id:
        return False

    blacklist_result = await ctx.call_tool(
        server="admin",
        tool="adm_list_course_blacklist",
        arguments={"page": 1, "page_size": 15, "fetch_all": True},
    )
    if not blacklist_result.get("success"):
        return False

    blacklist = (blacklist_result.get("data") or {}).get("blacklist", [])
    return any(str(entry.get("umu_id", "")) == umu_id for entry in blacklist)


async def _remove_from_blacklist_if_needed(
    ctx: SkillContext,
    group_id: str,
    umu_id: str | None,
    timeline: list[dict[str, Any]],
) -> str | None:
    """幂等地将讲师移出黑名单.

    返回实际执行了移除操作的 umu_id；若无需操作返回 None。
    """
    if not umu_id:
        return None

    if not await _is_user_blacklisted(ctx, umu_id):
        return None

    remove_result = await ctx.call_tool(
        server="admin",
        tool="adm_save_course_blacklist",
        arguments={"umu_id": umu_id, "action": "remove"},
    )
    timeline.append({
        "stage": "restore_remove_blacklist",
        "group_id": group_id,
        "umu_id": umu_id,
        "success": remove_result.get("success", False),
        "error": remove_result.get("error_message", ""),
        "data": remove_result.get("data"),
    })
    if not remove_result.get("success"):
        raise LifecycleError(
            f"移出黑名单失败: {remove_result.get('error_message')}",
            stage="restore_remove_blacklist",
        )
    return umu_id


async def _revoke_course_if_needed(
    ctx: SkillContext,
    group_id: str,
    timeline: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """幂等地撤销课程提交.

    平台不支持直接回到 -1（未提交），3（已撤销）是业务上等价的状态。
    若课程已为 3，则直接 no-op。
    """
    snapshot = await get_course_snapshot(ctx, group_id)
    current_status = snapshot["audit_status"]

    if current_status == 3:
        return _ok(data={"group_id": group_id, "audit_status": 3, "no_op": True})

    revoke_result = await ctx.call_tool(
        server="admin",
        tool="adm_audit_course",
        arguments={
            "group_ids": group_id,
            "action": "revoke",
            "reason": "测试结束恢复",
        },
    )

    if timeline is not None:
        timeline.append({
            "stage": "restore_revoke",
            "group_id": group_id,
            "success": revoke_result.get("success", False),
            "error": revoke_result.get("error_message", ""),
            "data": revoke_result.get("data"),
        })

    if not revoke_result.get("success"):
        raise LifecycleError(
            f"撤销课程 {group_id} 失败: {revoke_result.get('error_message')}",
            stage="final_restore",
        )

    # 二次确认平台状态
    await assert_audit_status(ctx, group_id, 3, "final_restore")
    return revoke_result


async def _restore_course_state(
    ctx: SkillContext,
    group_id: str,
    owner_umu_id: str | None,
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    """幂等地恢复单门课程与讲师状态.

    恢复目标：
    - 课程 audit_status 为 3（已撤销，平台不支持回到 -1）；
    - 讲师不在黑名单中。

    任意中间状态下重复调用都能安全到达一致终态。
    """
    report_progress(_SKILL_NAME, f"课程 {group_id} 开始恢复状态")

    # 1. 移出黑名单（幂等：只在仍在黑名单时操作）
    await _remove_from_blacklist_if_needed(ctx, group_id, owner_umu_id, timeline)

    # 2. 撤销课程到状态 3（幂等：已为 3 时 no-op）
    revoke_result = await _revoke_course_if_needed(ctx, group_id, timeline)

    report_progress(_SKILL_NAME, f"课程 {group_id} 恢复完成")
    return revoke_result


async def _run_lifecycle_for_course(
    ctx: SkillContext,
    group_id: str,
    restore_initial_state: bool,
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    """对单门课程执行完整生命周期测试.

    流程：提交 -> 通过 -> 撤销 -> 再次提交 -> 拒绝并加黑名单 -> 移出黑名单 -> 最终恢复。
    任意步骤失败都会触发恢复逻辑。
    """
    owner_umu_id: str | None = None
    access_code: str = ""

    # 提交后更新 access_code 与 owner_umu_id 的钩子，同时完成状态断言
    async def _after_submit() -> None:
        nonlocal access_code, owner_umu_id
        snapshot = await get_course_snapshot(ctx, group_id)
        if snapshot["audit_status"] != 0:
            raise LifecycleError(
                f"提交后课程 {group_id} 期望 audit_status=0, 实际={snapshot['audit_status']}",
                stage="submit",
            )
        access_code = snapshot["access_code"]
        owner_umu_id = await _find_owner_umu_id(ctx, access_code)

    # 拒绝前确保已获取 owner_umu_id
    async def _before_reject() -> None:
        nonlocal owner_umu_id
        if not owner_umu_id and access_code:
            owner_umu_id = await _find_owner_umu_id(ctx, access_code)

    stages: list[LifecycleStage] = [
        LifecycleStage(
            name="submit",
            server="teacher",
            tool="tch_submit_course_for_audit",
            arguments={"group_id": group_id},
            description="提交审核",
            after=_after_submit,
        ),
        LifecycleStage(
            name="approve",
            server="admin",
            tool="adm_audit_course",
            arguments={"group_ids": group_id, "action": "approve"},
            expected_status=1,
            description="通过审核",
        ),
        LifecycleStage(
            name="revoke_after_approve",
            server="admin",
            tool="adm_audit_course",
            arguments={
                "group_ids": group_id,
                "action": "revoke",
                "reason": "测试恢复",
            },
            expected_status=3,
            description="撤销提交",
        ),
        LifecycleStage(
            name="resubmit",
            server="teacher",
            tool="tch_submit_course_for_audit",
            arguments={"group_id": group_id},
            expected_status=0,
            description="再次提交",
        ),
        LifecycleStage(
            name="reject_with_blacklist",
            server="admin",
            tool="adm_audit_course",
            arguments={
                "group_ids": group_id,
                "action": "reject",
                "reason": "测试拒绝",
                "add_to_blacklist": True,
            },
            expected_status=2,
            description="拒绝并加入黑名单",
            before=_before_reject,
        ),
        LifecycleStage(
            name="remove_blacklist",
            server="admin",
            tool="adm_save_course_blacklist",
            arguments=lambda: {"umu_id": owner_umu_id or "", "action": "remove"},
            description="移出黑名单",
        ),
    ]

    try:
        await run_lifecycle_stages(
            ctx,
            _SKILL_NAME,
            group_id,
            stages,
            timeline,
        )

        # 黑名单移除后二次确认讲师已不在黑名单
        if owner_umu_id and await _is_user_blacklisted(ctx, owner_umu_id):
            raise LifecycleError(
                f"课程 {group_id} 讲师 {owner_umu_id} 仍在黑名单中",
                stage="remove_blacklist",
            )

        # G1: 最终恢复
        if restore_initial_state:
            final_restore = await _restore_course_state(
                ctx,
                group_id,
                owner_umu_id,
                timeline,
            )
            if not final_restore.get("success"):
                raise LifecycleError(
                    f"最终恢复课程 {group_id} 失败: {final_restore.get('error_message')}",
                    stage="final_restore",
                )

    except LifecycleError:
        # 失败时尝试恢复，然后向上抛出
        if restore_initial_state:
            try:
                await _restore_course_state(ctx, group_id, owner_umu_id, timeline)
            except LifecycleError as restore_err:
                # 恢复失败也记录在时间线中，但不覆盖原始错误
                timeline.append({
                    "stage": "recovery_failed",
                    "group_id": group_id,
                    "success": False,
                    "error": str(restore_err),
                })
        raise

    return _ok(data={"group_id": group_id, "completed": True})


async def _auto_select_unsubmitted(
    ctx: SkillContext,
    max_courses: int = 50,
) -> list[str]:
    """自动选择当前 Teacher 账号下 audit_status=-1 的课程.

    由于 tch_list_created_courses 不返回 audit_status，
    需要对列表中每门课程调用 tch_get_course 查询状态。
    """
    report_progress(_SKILL_NAME, "自动筛选未提交审核的课程")

    group_ids: list[str] = []
    page = 1
    checked = 0

    while len(group_ids) < max_courses:
        list_result = await ctx.call_tool(
            server="teacher",
            tool="tch_list_created_courses",
            arguments={"page": page, "page_size": 20, "order": "update_time"},
        )
        if not list_result.get("success"):
            raise LifecycleError(
                f"获取讲师课程列表失败: {list_result.get('error_message')}",
                stage="auto_select",
            )

        data = list_result.get("data") or {}
        courses = data.get("courses", [])
        pagination = data.get("pagination", {})

        if not courses:
            break

        for course in courses:
            if checked >= max_courses:
                break
            checked += 1
            group_id = course.get("group_id", "")
            if not group_id:
                continue

            try:
                status = (await get_course_snapshot(ctx, group_id)).get(
                    "audit_status", -1
                )
            except LifecycleError:
                continue

            if status == -1:
                group_ids.append(group_id)
                report_progress(
                    _SKILL_NAME,
                    f"发现未提交课程：{group_id} ({course.get('title', '')})",
                )

        total_pages = int(pagination.get("total_pages", 1) or 1)
        if page >= total_pages:
            break
        page += 1

    report_progress(_SKILL_NAME, f"自动筛选完成，共 {len(group_ids)} 门未提交课程")
    return group_ids


def _build_dry_run_plan(group_ids: list[str]) -> dict[str, Any]:
    """构造 dry_run 阶段的执行计划."""
    planned_steps: list[dict[str, Any]] = []
    for group_id in group_ids:
        planned_steps.extend([
            {"server": "teacher", "tool": "tch_get_course", "arguments": {"group_id": group_id}},
            {"server": "teacher", "tool": "tch_submit_course_for_audit", "arguments": {"group_id": group_id}},
            {"server": "teacher", "tool": "tch_get_course", "arguments": {"group_id": group_id}},
            {"server": "admin", "tool": "adm_list_course_audit_records", "arguments": {"access_code": "自动获取"}},
            {"server": "admin", "tool": "adm_audit_course", "arguments": {"group_ids": group_id, "action": "approve"}},
            {"server": "admin", "tool": "adm_audit_course", "arguments": {"group_ids": group_id, "action": "revoke"}},
            {"server": "teacher", "tool": "tch_submit_course_for_audit", "arguments": {"group_id": group_id}},
            {"server": "admin", "tool": "adm_audit_course", "arguments": {"group_ids": group_id, "action": "reject", "add_to_blacklist": True}},
            {"server": "admin", "tool": "adm_save_course_blacklist", "arguments": {"action": "remove"}},
            {"server": "admin", "tool": "adm_audit_course", "arguments": {"group_ids": group_id, "action": "revoke"}},
        ])

    return {
        "planned_steps": planned_steps,
        "estimated_calls": len(planned_steps) + len(group_ids),  # 含最终快照
        "affected_courses": len(group_ids),
    }


@skill(
    name="validate_course_audit_lifecycle",
    description="跨角色课程审核生命周期测试：Teacher 提交 -> Admin 通过/撤销/拒绝+黑名单/恢复，最终状态保持一致",
    required_servers=["teacher", "admin"],
    return_description="包含初始快照、状态变更时间线、最终快照与一致性检查的报告",
)
async def validate_course_audit_lifecycle(
    ctx: SkillContext,
    group_ids: list[str] | None = None,
    auto_select_unsubmitted: bool = False,
    max_auto_select: int = 10,
    restore_initial_state: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """执行课程审核生命周期测试.

    用于验证 umu skill 框架跨角色编排的有效性、准确性与使用便捷性。

    Args:
        ctx: Skill 上下文，用于跨角色调用子 MCP。
        group_ids: 测试课程 ID 列表。与 auto_select_unsubmitted 二选一。
        auto_select_unsubmitted: 为 True 时自动选择 Teacher 账号下 audit_status=-1 的课程。
        max_auto_select: 自动选择时的最大课程数，默认 10。
        restore_initial_state: 测试结束后是否恢复到初始状态，默认 True。
        dry_run: 为 True 时只输出计划与初始快照，不实际调用修改操作。

    Returns:
        标准返回信封，data 中包括 initial_snapshot、timeline、final_snapshot、
        consistent、known_side_effects 等字段。
    """
    # 参数校验与自动选择
    if not group_ids and not auto_select_unsubmitted:
        return _err(
            error_code="EMPTY_GROUP_IDS",
            error_message="group_ids 与 auto_select_unsubmitted 不能同时为空",
            suggested_action="请提供 group_ids 或将 auto_select_unsubmitted 设为 true",
            next_action="needs_user_input",
        )

    selected_group_ids: list[str] = []
    if group_ids:
        selected_group_ids = [str(g) for g in group_ids if g]

    if auto_select_unsubmitted:
        try:
            auto_selected = await _auto_select_unsubmitted(ctx, max_auto_select)
        except LifecycleError as e:
            return _err(
                error_code="AUTO_SELECT_FAILED",
                error_message=str(e),
                suggested_action="请确认 Teacher 已登录，或手动提供 group_ids",
            )
        selected_group_ids = list(dict.fromkeys(selected_group_ids + auto_selected))

    if not selected_group_ids:
        return _err(
            error_code="EMPTY_GROUP_IDS",
            error_message="未找到可用的测试课程",
            suggested_action="请确认存在 audit_status=-1 的课程，或手动提供 group_ids",
            next_action="needs_user_input",
        )

    timeline: list[dict[str, Any]] = []

    # 阶段 A：初始快照
    report_progress(_SKILL_NAME, f"开始采集 {len(selected_group_ids)} 门课程的初始快照")
    initial_snapshots: list[dict[str, Any]] = []
    try:
        for group_id in selected_group_ids:
            snapshot = await get_course_snapshot(ctx, group_id)
            initial_snapshots.append(snapshot)
    except LifecycleError as e:
        return _err(
            error_code="SNAPSHOT_FAILED",
            error_message=str(e),
            suggested_action="请确认 group_id 正确且 Teacher 已登录",
        )

    if dry_run:
        plan = _build_dry_run_plan(selected_group_ids)
        return _ok(
            data={
                "initial_snapshot": initial_snapshots,
                "timeline": [],
                "final_snapshot": initial_snapshots,
                "consistent": True,
                "dry_run": True,
                "known_side_effects": [],
                **plan,
            },
            suggested_action="确认无误后将 dry_run 设为 false 执行真实测试",
        )

    # 阶段 B-G：对每门课程执行生命周期测试
    report_progress(_SKILL_NAME, f"开始对 {len(selected_group_ids)} 门课程执行生命周期测试")
    completed_groups: list[str] = []
    failed_groups: list[dict[str, Any]] = []

    for index, group_id in enumerate(selected_group_ids, start=1):
        report_progress(_SKILL_NAME, f"[{index}/{len(selected_group_ids)}] 处理课程 {group_id}")
        try:
            await _run_lifecycle_for_course(
                ctx,
                group_id,
                restore_initial_state,
                timeline,
            )
            completed_groups.append(group_id)
            report_progress(_SKILL_NAME, f"[{index}/{len(selected_group_ids)}] 课程 {group_id} 完成")
        except LifecycleError as e:
            failed_groups.append({
                "group_id": group_id,
                "stage": e.stage,
                "error": str(e),
            })
            report_progress(
                _SKILL_NAME,
                f"[{index}/{len(selected_group_ids)}] 课程 {group_id} 在阶段 {e.stage} 失败: {e}",
            )

    # 阶段 H：最终快照与一致性检查
    report_progress(_SKILL_NAME, "采集最终快照并进行一致性检查")
    final_snapshots: list[dict[str, Any]] = []
    try:
        for group_id in selected_group_ids:
            snapshot = await get_course_snapshot(ctx, group_id)
            final_snapshots.append(snapshot)
    except LifecycleError as e:
        return _err(
            error_code="FINAL_SNAPSHOT_FAILED",
            error_message=str(e),
            data={
                "initial_snapshot": initial_snapshots,
                "timeline": timeline,
                "completed_groups": completed_groups,
                "failed_groups": failed_groups,
            },
        )

    # 一致性检查：
    # - 所有课程最终 audit_status 应为 3（已撤销），因为平台不支持回到 -1；
    # - 无失败的课程组。
    consistent = len(failed_groups) == 0 and all(
        snapshot.get("audit_status") == 3 for snapshot in final_snapshots
    )

    report = {
        "initial_snapshot": initial_snapshots,
        "timeline": timeline,
        "final_snapshot": final_snapshots,
        "completed_groups": completed_groups,
        "failed_groups": failed_groups,
        "consistent": consistent,
        "known_side_effects": [
            "reject_num、current_reject_times、release_num 等平台计数器无法清零",
        ],
    }

    if failed_groups or not consistent:
        return _err(
            error_code="LIFECYCLE_TEST_INCONSISTENT",
            error_message="部分课程状态未恢复到预期，请查看 failed_groups 与 timeline",
            data=report,
            suggested_action="检查失败阶段日志，必要时人工在 UMU 后台恢复课程状态",
        )

    report_progress(_SKILL_NAME, "全部课程生命周期测试完成，状态一致")
    return _ok(
        data=report,
        suggested_action="测试完成，课程与讲师状态已恢复",
    )


__all__ = ["validate_course_audit_lifecycle"]
