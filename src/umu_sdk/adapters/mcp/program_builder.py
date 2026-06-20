# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""学习项目创建编排器."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .course_builder import CourseBuilder
from .image_upload import ImageUploader

logger = logging.getLogger("umu.mcp.program_builder")


class ProgramBuilder:
    """学习项目创建编排器.

    封装创建学习项目、添加课程、配置证书、开关积分等后端调用。
    """

    def __init__(self, client: Any, base_url: str):
        """初始化.

        Args:
            client: UMUClient 实例
            base_url: UMU 基础 URL
        """
        self.client = client
        self.base_url = base_url
        self._course_builder = CourseBuilder(client)

    # ------------------------------------------------------------------
    # 创建项目
    # ------------------------------------------------------------------

    def create_program(
        self,
        title: str,
        desc_plain: str = "",
        desc_richtext: str = "",
        cover_path: str = "",
        bg_path: str = "",
        tags: list[str] | None = None,
        category_ids: list[str] | None = None,
        category_names: list[str] | None = None,
        start_time: str = "",
        end_time: str = "",
    ) -> dict[str, Any]:
        """创建学习项目.

        Args:
            title: 项目标题
            desc_plain: 纯文本介绍
            desc_richtext: 富文本介绍（HTML）
            cover_path: 本地封面图路径
            bg_path: 本地背景图路径
            tags: 标签列表
            category_ids: 分类 ID 列表
            category_names: 分类名称列表（与 category_ids 二选一，名称优先）
            start_time: 开始时间戳字符串
            end_time: 结束时间戳字符串

        Returns:
            {"program_id": "..."}
        """
        multimedia_id = ""
        if desc_richtext:
            try:
                multimedia_id = self._course_builder._create_fulltext(
                    desc_richtext, ref_type="program"
                )
                logger.info("项目富文本创建成功: multimedia_id=%s", multimedia_id)
            except Exception as e:
                logger.warning("项目富文本创建失败（非致命）: %s", e)

        cover_url = ""
        if cover_path:
            try:
                uploader = ImageUploader(self.client, self.base_url)
                result = uploader.upload(cover_path, media_type="picweike")
                cover_url = result.file_url
                logger.info("项目封面上传成功: %s", cover_url)
            except Exception as e:
                logger.warning("项目封面上传失败（非致命）: %s", e)

        bg_url = ""
        if bg_path:
            try:
                uploader = ImageUploader(self.client, self.base_url)
                result = uploader.upload(bg_path, media_type="picweike")
                bg_url = result.file_url
                logger.info("项目背景上传成功: %s", bg_url)
            except Exception as e:
                logger.warning("项目背景上传失败（非致命）: %s", e)

        category_arr = self._resolve_categories(category_ids, category_names)
        tags_arr = [{"tag": str(tag)} for tag in (tags or [])]

        program_info: dict[str, Any] = {
            "program_id": 0,
            "program_title": title,
            "desc": desc_plain or "",
            "multimedia_type": 1 if multimedia_id else 0,
            "multimedia_id": multimedia_id,
            "tags": tags_arr,
            "unlock_type": 1,
            "setup": {
                "pc_skin_id": 1,
                "skin_data": {
                    "1": {"show_banner": 1},
                    "2": {"show_banner": 0},
                    "3": {"show_banner": 0},
                    "4": {"show_banner": 0},
                    "5": {"show_banner": 0},
                },
                "show_banner": 1,
                "enable_certificate": 0,
                "skin_id": 1,
                "sort": "asc",
            },
        }
        if cover_url:
            program_info["head_img"] = cover_url
        if bg_url:
            program_info["setup"]["bg_img"] = bg_url
        if start_time:
            program_info["start_time"] = str(start_time)
        if end_time:
            program_info["end_time"] = str(end_time)

        data = {
            "program_info": program_info,
            "category_arr": category_arr,
        }

        payload = {
            "program_id": "0",
            "module": "program",
            "data": json.dumps(data, ensure_ascii=False),
        }

        resp = self.client.post(
            self.client.desktop_url("/api/program/updateinfo"),
            data=payload,
        )
        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "创建学习项目失败"))

        program_id = str(resp.get("data", {}).get("program_id", ""))
        if not program_id:
            raise RuntimeError("创建学习项目成功，但响应中无 program_id")

        logger.info("学习项目创建成功: program_id=%s", program_id)
        return {"program_id": program_id}

    # ------------------------------------------------------------------
    # 添加课程
    # ------------------------------------------------------------------

    def add_courses(
        self,
        program_id: str,
        modules: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """按模块批量添加课程.

        Args:
            program_id: 学习项目 ID
            modules: 模块列表，每项包含 module_title 与 course_ids。
                若 module_id 已存在，也可传入 module_id 直接使用。

        Returns:
            {"added": [...], "failed": [...]}
        """
        added: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for module in modules:
            module_id = str(module.get("module_id") or "")
            module_title = str(module.get("module_title") or "")
            course_ids = module.get("course_ids") or []

            if not course_ids:
                continue

            if not module_id:
                if not module_title:
                    for course_id in course_ids:
                        failed.append({"course_id": course_id, "reason": "缺少模块标题"})
                    continue
                first_course = course_ids[0]
                rest_courses = course_ids[1:]
                resp = self._add_single_course(program_id, "0", first_course, module_title)
                if resp.get("status") in (True, "true") or resp.get("error_code") == 0:
                    module_id = str(resp.get("data", {}).get("module_id", ""))
                    obj_id = str(resp.get("data", {}).get("obj_id", ""))
                    added.append({"course_id": first_course, "module_id": module_id, "obj_id": obj_id})
                else:
                    failed.append({"course_id": first_course, "reason": resp.get("error", "创建模块失败")})
                    continue
                for course_id in rest_courses:
                    self._add_with_retry(program_id, module_id, course_id, added, failed)
            else:
                for course_id in course_ids:
                    self._add_with_retry(program_id, module_id, course_id, added, failed)

        return {"added": added, "failed": failed}

    def _add_single_course(
        self,
        program_id: str,
        module_id: str,
        course_id: str,
        module_title: str = "",
    ) -> dict[str, Any]:
        """调用 addprogramcourse 添加单个课程."""
        payload: dict[str, str] = {
            "program_id": str(program_id),
            "module_id": str(module_id),
            "course_id": str(course_id),
        }
        if module_title:
            payload["module_title"] = module_title
        return self.client.post(
            self.client.desktop_url("/api/program/addprogramcourse"),
            data=payload,
        )

    def _add_with_retry(
        self,
        program_id: str,
        module_id: str,
        course_id: str,
        added: list[dict[str, Any]],
        failed: list[dict[str, Any]],
    ) -> None:
        """添加课程，遇到频率限制时退避重试."""
        max_retries = 3
        for attempt in range(max_retries):
            resp = self._add_single_course(program_id, module_id, course_id)
            if resp.get("status") in (True, "true") or resp.get("error_code") == 0:
                added.append({
                    "course_id": course_id,
                    "module_id": module_id,
                    "obj_id": str(resp.get("data", {}).get("obj_id", "")),
                })
                return
            err = resp.get("error", "")
            if "过于频繁" in err and attempt < max_retries - 1:
                wait = 0.5 * (2 ** attempt)
                logger.warning("添加课程触发频率限制，%ss 后重试", wait)
                time.sleep(wait)
                continue
            failed.append({"course_id": course_id, "reason": err})
            return

    # ------------------------------------------------------------------
    # 证书与积分
    # ------------------------------------------------------------------

    def configure_certificate(
        self,
        program_id: str,
        theme_id: str = "",
        text: str = "",
        teacher_name: str = "",
    ) -> dict[str, Any]:
        """配置学习项目证书.

        未提供 theme_id 时，自动查询 template_type=1 的第一个模板。
        """
        if not theme_id:
            resp = self.client.get(
                self.client.desktop_url("/uapi/v1/program/get-certificate-template-list"),
                params={
                    "t": str(int(time.time() * 1000)),
                    "template_type": "1",
                    "page": "1",
                    "size": "50",
                },
            )
            if resp.get("error_code") != 0:
                raise RuntimeError(resp.get("error_message", "获取证书模板失败"))
            templates = resp.get("data", {}).get("list", [])
            if not templates:
                raise RuntimeError("未找到可用证书模板")
            theme_id = str(templates[0].get("id", ""))

        certificate_data = {
            "text": text or "成功完成学习项目",
            "teacher_name": teacher_name or "",
            "theme_id": theme_id,
            "theme_type": "1",
        }
        resp = self.client.post(
            self.client.desktop_url("/uapi/v1/program/save-certificate"),
            data={
                "program_id": str(program_id),
                "certificate_data": json.dumps(certificate_data, ensure_ascii=False),
            },
        )
        if resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error_message", "保存证书失败"))
        return resp.get("data", {})

    def set_points_status(self, program_id: str, enabled: bool) -> dict[str, Any]:
        """开启或关闭学习项目积分."""
        resp = self.client.post(
            self.client.desktop_url("/uapi/v1/program/change-program-points-status"),
            data={
                "program_id": str(program_id),
                "is_open_point": "1" if enabled else "0",
            },
        )
        if resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error_message", "设置积分状态失败"))
        return resp.get("data", {})

    def search_courses(
        self,
        program_id: str,
        keywords: str = "",
        creater_name: str = "",
        page: int = 1,
        page_size: int = 10,
    ) -> tuple[list[dict[str, Any]], int]:
        """搜索可加入项目的课程."""
        params: dict[str, str] = {
            "t": str(int(time.time() * 1000)),
            "program_id": str(program_id),
            "page": str(page),
            "size": str(page_size),
        }
        if keywords:
            params["keywords"] = keywords
        if creater_name:
            params["creater_name"] = creater_name

        resp = self.client.get(
            self.client.desktop_url("/api/program/searchcoursetoprogram"),
            params=params,
        )
        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "搜索课程失败"))

        data = resp.get("data", {})
        page_info = data.get("page_info", {})
        total = int(page_info.get("list_total_num", 0) or 0)
        return data.get("list", []), total

    def _resolve_categories(
        self,
        category_ids: list[str] | None,
        category_names: list[str] | None,
    ) -> list[dict[str, str]]:
        """解析分类，category_names 优先."""
        final_ids: list[str] = []
        if category_names:
            resolved = self._course_builder.resolve_category_names(category_names)
            final_ids = [cid for cid, _, _ in resolved]
        elif category_ids:
            final_ids = [str(cid) for cid in category_ids]
        return [{"category_id": cid} for cid in final_ids]

