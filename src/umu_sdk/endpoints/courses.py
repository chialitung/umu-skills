# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""课程管理端点.

对应 UMU 系统中"我的课程"模块的 API 接口.
基于逆向分析，UMU 课程相关 API 使用以下路径模式：
- 课程列表: /uapi/v1/user/get 或 /api/homepage/gethomepageinfo
- 课程详情: /course/?groupId=XXX&sKey=YYY

由于 UMU 实际 API 仍在逆向过程中，此处提供通用 CRUD 接口框架，
后续根据 Phase 2 分析结果填充具体端点.
"""

from __future__ import annotations

import sys

import httpx

from ..core.models import (
    Course,
    CourseRule,
    CreateCourseRequest,
    ListCoursesParams,
    PaginatedResponse,
    UpdateCourseRequest,
)


class CourseEndpoint:
    """课程管理端点."""

    def __init__(self, http: httpx.Client):
        self.http = http
        self.base_path = "/api/courses"

    def list(self, params: ListCoursesParams | None = None) -> PaginatedResponse:
        """获取课程列表.

        Args:
            params: 查询参数

        Returns:
            分页课程列表
        """
        query = params.model_dump(by_alias=True, exclude_none=True) if params else {}
        response = self.http.get(self.base_path, params=query)
        response.raise_for_status()
        return PaginatedResponse(**response.json())

    def get(self, course_id: str) -> Course:
        """获取课程详情.

        Args:
            course_id: 课程 ID

        Returns:
            课程详情
        """
        response = self.http.get(f"{self.base_path}/{course_id}")
        response.raise_for_status()
        return Course(**response.json())

    def create(self, data: CreateCourseRequest) -> Course:
        """创建课程.

        Args:
            data: 创建课程请求

        Returns:
            创建后的课程
        """
        response = self.http.post(
            self.base_path,
            json=data.model_dump(by_alias=True, exclude_none=True),
        )
        response.raise_for_status()
        return Course(**response.json())

    def update(self, course_id: str, data: UpdateCourseRequest) -> Course:
        """更新课程.

        Args:
            course_id: 课程 ID
            data: 更新课程请求

        Returns:
            更新后的课程
        """
        response = self.http.put(
            f"{self.base_path}/{course_id}",
            json=data.model_dump(by_alias=True, exclude_none=True),
        )
        response.raise_for_status()
        return Course(**response.json())

    def delete(self, course_id: str) -> None:
        """删除课程.

        Args:
            course_id: 课程 ID
        """
        response = self.http.delete(f"{self.base_path}/{course_id}")
        response.raise_for_status()

    def publish(self, course_id: str) -> Course:
        """发布课程.

        Args:
            course_id: 课程 ID

        Returns:
            发布后的课程
        """
        response = self.http.post(f"{self.base_path}/{course_id}/publish")
        response.raise_for_status()
        return Course(**response.json())

    def unpublish(self, course_id: str) -> Course:
        """下架课程.

        Args:
            course_id: 课程 ID

        Returns:
            下架后的课程
        """
        response = self.http.post(f"{self.base_path}/{course_id}/unpublish")
        response.raise_for_status()
        return Course(**response.json())

    def set_rules(self, course_id: str, rules: CourseRule) -> CourseRule:
        """设置课程规则.

        Args:
            course_id: 课程 ID
            rules: 课程规则

        Returns:
            设置后的规则
        """
        response = self.http.put(
            f"{self.base_path}/{course_id}/rules",
            json=rules.model_dump(by_alias=True, exclude_none=True),
        )
        response.raise_for_status()
        return CourseRule(**response.json())

    def get_rules(self, course_id: str) -> CourseRule:
        """获取课程规则.

        Args:
            course_id: 课程 ID

        Returns:
            课程规则
        """
        response = self.http.get(f"{self.base_path}/{course_id}/rules")
        response.raise_for_status()
        return CourseRule(**response.json())

    def iterate_all(
        self,
        params: ListCoursesParams | None = None,
        page_size: int = 100,
    ) -> list[Course]:
        """遍历所有课程（自动分页）.

        Args:
            params: 基础查询参数（page 会被覆盖）
            page_size: 每页数量

        Returns:
            所有课程列表
        """
        all_courses: list[Course] = []
        page = 1
        total_all = 0

        while True:
            query_params = params or ListCoursesParams()
            query = query_params.model_copy(update={"page": page, "page_size": page_size})

            result = self.list(query)
            all_courses.extend([Course(**item) for item in result.data])
            total_all = result.total

            # 控制台进度提示（输出到 stderr，避免干扰 MCP stdio 协议）
            progress_pct = ""
            if total_all > 0:
                pct = min(100, int(len(all_courses) / total_all * 100))
                progress_pct = f" ({pct}%)"
            if total_all > 0 and page == 1:
                estimated_pages = max(1, (total_all + page_size - 1) // page_size)
                print(
                    f"[CourseEndpoint.iterate_all] 共 {total_all} 条，"
                    f"预计 {estimated_pages} 页",
                    file=sys.stderr,
                )
            print(
                f"[CourseEndpoint.iterate_all] 已获取第 {page} 页，"
                f"累计 {len(all_courses)} / {total_all} 条{progress_pct}",
                file=sys.stderr,
            )

            if page >= result.total_pages:
                print(
                    f"[CourseEndpoint.iterate_all] 获取完成，"
                    f"共 {len(all_courses)} 条，合计 {page} 页",
                    file=sys.stderr,
                )
                break
            page += 1
            # 安全上限：最多 50 页
            if page > 50:
                print(
                    f"[CourseEndpoint.iterate_all] 警告：达到 50 页安全上限，停止获取"
                    f"（已获取 {len(all_courses)} 条）",
                    file=sys.stderr,
                )
                break

        return all_courses
