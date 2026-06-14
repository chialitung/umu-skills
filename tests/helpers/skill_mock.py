"""Skill 测试辅助工具.

封装 mock SkillContext 与按序响应队列，降低生命周期 Skill 的单元测试成本。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock


def ok_response(data: Any = None) -> dict[str, Any]:
    """构造标准成功响应."""
    return {"success": True, "data": data, "error_code": "", "error_message": ""}


def err_response(
    error_code: str,
    error_message: str,
    data: Any = None,
) -> dict[str, Any]:
    """构造标准失败响应."""
    return {
        "success": False,
        "data": data,
        "error_code": error_code,
        "error_message": error_message,
    }


class SkillMockBuilder:
    """按 Skill 调用顺序构建 mock 响应队列.

    典型用法：
        builder = SkillMockBuilder()
        builder.add_snapshot("g-001", audit_status=-1)
        builder.add_submit("g-001", audit_status=0)
        builder.add_audit("g-001", "approve", audit_status=1)
        builder.add_final_snapshot("g-001", audit_status=3)
        ctx = builder.build()
        result = await my_skill(ctx=ctx, group_ids=["g-001"])
    """

    def __init__(self) -> None:
        self._responses: list[dict[str, Any]] = []
        self._calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def add_response(self, response: dict[str, Any]) -> "SkillMockBuilder":
        """追加任意响应."""
        self._responses.append(response)
        return self

    def add_course_info(
        self,
        group_id: str,
        audit_status: int,
        access_code: str = "abc123",
        title: str = "测试课程",
    ) -> "SkillMockBuilder":
        """追加 tch_get_course 返回的课程信息."""
        return self.add_response(
            ok_response({
                "group_id": group_id,
                "title": title,
                "access_code": access_code,
                "audit_status": str(audit_status),
                "release_status": "2" if audit_status >= 0 else "0",
            })
        )

    add_snapshot = add_course_info

    def add_final_snapshot(
        self,
        group_id: str,
        audit_status: int,
        access_code: str = "abc123",
    ) -> "SkillMockBuilder":
        """追加最终快照响应."""
        return self.add_course_info(group_id, audit_status, access_code)

    def add_submit(
        self,
        group_id: str,
        audit_status: int = 0,
        success: bool = True,
        error_message: str = "",
    ) -> "SkillMockBuilder":
        """追加 tch_submit_course_for_audit 响应."""
        if success:
            return self.add_response(
                ok_response({
                    "group_id": group_id,
                    "audit_status": audit_status,
                    "release_status": "2",
                })
            )
        return self.add_response(
            err_response("SUBMIT_FAILED", error_message or "提交失败")
        )

    def add_audit(
        self,
        group_id: str,
        action: str,
        audit_status: int,
        success: bool = True,
        error_message: str = "",
        add_to_blacklist: bool = False,
    ) -> "SkillMockBuilder":
        """追加 adm_audit_course 响应."""
        if success:
            data: dict[str, Any] = {
                "group_ids": group_id,
                "action": action,
                "audit_status": audit_status,
            }
            if add_to_blacklist:
                data["add_to_blacklist"] = True
            return self.add_response(ok_response(data))
        return self.add_response(
            err_response("AUDIT_FAILED", error_message or f"{action} 失败")
        )

    def add_audit_records(
        self,
        access_code: str,
        umu_id: str = "u-001",
        teacher_id: str = "t-001",
    ) -> "SkillMockBuilder":
        """追加 adm_list_course_audit_records 响应."""
        return self.add_response(
            ok_response({
                "records": [
                    {
                        "group_id": "g-001",
                        "access_code": access_code,
                        "umu_id": umu_id,
                        "teacher_id": teacher_id,
                        "is_blacklist": False,
                    }
                ],
                "total": 1,
                "pagination": {"total_all": 1, "current_page": 1, "page_size": 20},
            })
        )

    def add_blacklist_list(
        self,
        umu_id: str | None = None,
    ) -> "SkillMockBuilder":
        """追加 adm_list_course_blacklist 响应."""
        blacklist: list[dict[str, Any]] = []
        if umu_id:
            blacklist.append({"umu_id": umu_id, "user_name": "讲师"})
        return self.add_response(
            ok_response({
                "blacklist": blacklist,
                "total": len(blacklist),
                "pagination": {
                    "total_all": len(blacklist),
                    "current_page": 1,
                    "page_size": 15,
                },
            })
        )

    def add_blacklist_save(
        self,
        umu_id: str,
        action: str,
        success: bool = True,
        error_message: str = "",
    ) -> "SkillMockBuilder":
        """追加 adm_save_course_blacklist 响应."""
        if success:
            return self.add_response(
                ok_response({"umu_id": umu_id, "action": action})
            )
        return self.add_response(
            err_response("BLACKLIST_SAVE_FAILED", error_message or "黑名单操作失败")
        )

    def add_created_courses(
        self,
        courses: list[dict[str, Any]],
        total_pages: int = 1,
    ) -> "SkillMockBuilder":
        """追加 tch_list_created_courses 响应."""
        return self.add_response(
            ok_response({
                "courses": courses,
                "pagination": {
                    "total": len(courses),
                    "total_pages": total_pages,
                    "current_page": 1,
                    "page_size": 20,
                },
            })
        )

    def extend(self, responses: list[dict[str, Any]]) -> "SkillMockBuilder":
        """批量追加响应."""
        self._responses.extend(responses)
        return self

    def build(self) -> MagicMock:
        """构造并返回配置好的 mock SkillContext."""
        ctx = MagicMock()
        responses = list(self._responses)
        calls: list[tuple[str, str, dict[str, Any] | None]] = []
        idx = 0

        async def fake_call_tool(
            server: str,
            tool: str,
            arguments: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            nonlocal idx
            calls.append((server, tool, arguments))
            if idx >= len(responses):
                return ok_response({})
            resp = responses[idx]
            idx += 1
            return resp

        ctx.call_tool = AsyncMock(side_effect=fake_call_tool)
        ctx.calls = calls
        return ctx

    def inject_response(
        self,
        index: int,
        response: dict[str, Any],
    ) -> "SkillMockBuilder":
        """在指定索引位置替换响应（用于失败点测试）."""
        if index < 0 or index >= len(self._responses):
            raise IndexError(f"响应索引 {index} 越界，当前共 {len(self._responses)} 个响应")
        self._responses[index] = response
        return self

    @property
    def responses(self) -> list[dict[str, Any]]:
        """返回当前响应队列（便于调试，返回副本避免误改）."""
        return list(self._responses)
