# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""学习项目创建编排器."""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
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
