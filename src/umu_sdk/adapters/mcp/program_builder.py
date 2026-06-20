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
        cover_image_url: str = "",
        bg_image_url: str = "",
        tags: list[str] | None = None,
        category_ids: list[str] | None = None,
        category_names: list[str] | None = None,
        start_time: str = "",
        end_time: str = "",
        skin_id: int | str = 1,
        pc_skin_id: int | str = 1,
        show_banner: bool = True,
        unlock_type: int | str = 1,
        show_type: int | str = 1,
        open_module: int | str = 1,
        sort: str = "asc",
        enable_certificate: bool | int = False,
    ) -> dict[str, Any]:
        """创建学习项目.

        Args:
            title: 项目标题
            desc_plain: 纯文本介绍
            desc_richtext: 富文本介绍（HTML）
            cover_path: 本地封面图路径
            bg_path: 本地背景图路径
            cover_image_url: 封面图 URL（与 cover_path 二选一，URL 优先）
            bg_image_url: 背景图 URL（与 bg_path 二选一，URL 优先）
            tags: 标签列表
            category_ids: 分类 ID 列表
            category_names: 分类名称列表（与 category_ids 二选一，名称优先）
            start_time: 开始时间戳字符串
            end_time: 结束时间戳字符串
            skin_id: 皮肤 ID（默认 1）
            pc_skin_id: PC 皮肤 ID（默认 1）
            show_banner: 是否显示 banner（默认 True）
            unlock_type: 解锁类型（默认 1）
            show_type: 显示类型（默认 1）
            open_module: 开放模块（默认 1）
            sort: 排序方式（默认 asc）
            enable_certificate: 是否启用证书（默认 False）

        Returns:
            {"program_id": "..."}
        """
        skin_id_str = str(skin_id)
        pc_skin_id_str = str(pc_skin_id)
        show_banner_int = 1 if show_banner else 0
        skin_data = {
            "1": {"show_banner": 1 if skin_id_str == "1" else 0},
            "2": {"show_banner": 1 if skin_id_str == "2" else 0},
            "3": {"show_banner": 1 if skin_id_str == "3" else 0},
            "4": {"show_banner": 1 if skin_id_str == "4" else 0},
            "5": {"show_banner": 1 if skin_id_str == "5" else 0},
        }

        multimedia_id = ""
        if desc_richtext:
            try:
                multimedia_id = self._course_builder._create_fulltext(
                    desc_richtext, ref_type="program"
                )
                logger.info("项目富文本创建成功: multimedia_id=%s", multimedia_id)
            except Exception as e:
                logger.warning("项目富文本创建失败（非致命）: %s", e)

        cover_url = cover_image_url or ""
        if not cover_url and cover_path:
            try:
                uploader = ImageUploader(self.client, self.base_url)
                result = uploader.upload(cover_path, media_type="picweike")
                cover_url = result.file_url
                logger.info("项目封面上传成功: %s", cover_url)
            except Exception as e:
                logger.warning("项目封面上传失败（非致命）: %s", e)

        bg_url = bg_image_url or ""
        if not bg_url and bg_path:
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
            "unlock_type": str(unlock_type),
            "show_type": str(show_type),
            "open_module": str(open_module),
            "setup": {
                "pc_skin_id": pc_skin_id_str,
                "skin_data": skin_data,
                "show_banner": show_banner_int,
                "enable_certificate": 1 if enable_certificate else 0,
                "skin_id": skin_id_str,
                "sort": sort,
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

    # ------------------------------------------------------------------
    # 修改项目
    # ------------------------------------------------------------------

    def get_program(self, program_id: str) -> dict[str, Any]:
        """获取学习项目详情."""
        resp = self.client.get(
            self.client.desktop_url("/api/program/getinfo"),
            params={
                "t": str(int(time.time() * 1000)),
                "module": "program",
                "program_id": str(program_id),
            },
        )
        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取学习项目详情失败"))
        return resp.get("data", {})

    def update_program(
        self,
        program_id: str,
        title: str | None = None,
        desc_plain: str | None = None,
        desc_richtext: str | None = None,
        cover_path: str | None = None,
        bg_path: str | None = None,
        cover_image_url: str | None = None,
        bg_image_url: str | None = None,
        tags: list[str] | None = None,
        category_ids: list[str] | None = None,
        category_names: list[str] | None = None,
        skin_id: int | str | None = None,
        pc_skin_id: int | str | None = None,
        show_banner: bool | None = None,
        unlock_type: int | str | None = None,
        show_type: int | str | None = None,
        open_module: int | str | None = None,
        sort: str | None = None,
        enable_certificate: bool | int | None = None,
    ) -> dict[str, Any]:
        """修改学习项目基本信息.

        未提供的字段保持原值，需要先调用 getinfo 获取当前配置。
        """
        info = self.get_program(program_id)
        program_info = info.get("program_info", {})
        if not program_info:
            raise RuntimeError("无法获取学习项目当前信息")

        setup = program_info.get("setup", {})
        skin_data = setup.get("skin_data", {
            "1": {"show_banner": 1},
            "2": {"show_banner": 0},
            "3": {"show_banner": 0},
            "4": {"show_banner": 0},
            "5": {"show_banner": 0},
        })

        multimedia_id = str(program_info.get("multimedia_id", ""))
        multimedia_type = int(program_info.get("multimedia_type", 0) or 0)

        if desc_richtext is not None:
            if multimedia_id:
                self._course_builder._update_fulltext(
                    multimedia_id, desc_richtext, program_id, ref_type="program"
                )
                logger.info("项目富文本更新成功: multimedia_id=%s", multimedia_id)
            else:
                try:
                    multimedia_id = self._course_builder._create_fulltext(
                        desc_richtext, ref_type="program"
                    )
                    multimedia_type = 1
                    logger.info("项目富文本创建成功: multimedia_id=%s", multimedia_id)
                except Exception as e:
                    logger.warning("项目富文本创建失败（非致命）: %s", e)

        cover_url = program_info.get("head_img", "")
        if cover_image_url is not None:
            cover_url = cover_image_url
        elif cover_path is not None:
            if cover_path:
                try:
                    uploader = ImageUploader(self.client, self.base_url)
                    result = uploader.upload(cover_path, media_type="picweike")
                    cover_url = result.file_url
                    logger.info("项目封面上传成功: %s", cover_url)
                except Exception as e:
                    logger.warning("项目封面上传失败（非致命）: %s", e)
            else:
                cover_url = ""

        bg_url = setup.get("bg_img", "")
        if bg_image_url is not None:
            bg_url = bg_image_url
        elif bg_path is not None:
            if bg_path:
                try:
                    uploader = ImageUploader(self.client, self.base_url)
                    result = uploader.upload(bg_path, media_type="picweike")
                    bg_url = result.file_url
                    logger.info("项目背景上传成功: %s", bg_url)
                except Exception as e:
                    logger.warning("项目背景上传失败（非致命）: %s", e)
            else:
                bg_url = ""

        final_skin_id = str(setup.get("skin_id", 1))
        if skin_id is not None:
            final_skin_id = str(skin_id)
        final_pc_skin_id = str(setup.get("pc_skin_id", final_skin_id))
        if pc_skin_id is not None:
            final_pc_skin_id = str(pc_skin_id)
        final_show_banner = setup.get("show_banner", 1)
        if show_banner is not None:
            final_show_banner = 1 if show_banner else 0

        final_tags = program_info.get("tags", [])
        if tags is not None:
            final_tags = [{"tag": str(tag)} for tag in tags]

        final_sort = setup.get("sort", "asc")
        if sort is not None:
            final_sort = sort

        final_enable_certificate = setup.get("enable_certificate", 0)
        if enable_certificate is not None:
            final_enable_certificate = 1 if enable_certificate else 0

        # 分类：始终回传当前或新解析的分类
        current_categories = info.get("category_arr", [])
        if category_ids is not None or category_names is not None:
            category_arr = self._resolve_categories(category_ids, category_names)
        else:
            category_arr = [{"category_id": str(c.get("id", ""))} for c in current_categories]

        new_program_info: dict[str, Any] = {
            "program_id": str(program_id),
            "program_title": title if title is not None else program_info.get("program_title", ""),
            "desc": desc_plain if desc_plain is not None else program_info.get("desc", ""),
            "setup": {
                "pc_skin_id": final_pc_skin_id,
                "skin_data": skin_data,
                "show_banner": final_show_banner,
                "bg_img": bg_url,
                "enable_certificate": final_enable_certificate,
                "skin_id": final_skin_id,
                "sort": final_sort,
            },
            "multimedia_type": multimedia_type,
            "multimedia_id": multimedia_id,
            "open_module": str(open_module) if open_module is not None else program_info.get("open_module", "1"),
            "unlock_type": str(unlock_type) if unlock_type is not None else program_info.get("unlock_type", "1"),
            "show_type": str(show_type) if show_type is not None else program_info.get("show_type", "1"),
            "tags": final_tags,
        }
        if cover_url or cover_image_url is not None or cover_path is not None:
            new_program_info["head_img"] = cover_url

        data: dict[str, Any] = {
            "program_info": new_program_info,
            "category_arr": category_arr,
        }

        resp = self.client.post(
            self.client.desktop_url("/api/program/updateinfo"),
            data={
                "program_id": str(program_id),
                "module": "program",
                "data": json.dumps(data, ensure_ascii=False),
            },
        )
        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "修改学习项目失败"))

        logger.info("学习项目修改成功: program_id=%s", program_id)
        return {"program_id": str(program_id)}

    def update_modules(
        self,
        program_id: str,
        modules: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """修改学习项目模块信息.

        modules 中每项可包含 module_id、module_title、module_desc、module_desc_richtext、
        order_index 以及 group_list。group_list 中的元素需要包含 id（module_group_id）、
        group_id、order_index、is_require、lesson_type。
        """
        info = self.get_program(program_id)
        program_info = info.get("program_info", {})
        if not program_info:
            raise RuntimeError("无法获取学习项目当前信息")

        existing_modules = {str(m.get("module_id")): m for m in info.get("module_list", [])}

        updated_modules: list[dict[str, Any]] = []
        for module in modules:
            module_id = str(module.get("module_id", ""))
            if not module_id or module_id not in existing_modules:
                raise RuntimeError(f"模块 {module_id} 不存在")
            existing = existing_modules[module_id]
            updated: dict[str, Any] = {
                "module_id": module_id,
                "module_title": module.get("module_title", existing.get("module_title", "")),
                "module_desc": module.get("module_desc", existing.get("module_desc", "")),
                "multimedia_type": str(existing.get("multimedia_type", 1)),
                "multimedia_id": str(existing.get("multimedia_id", "")),
                "order_index": module.get("order_index", existing.get("order_index", 0)),
            }

            # 模块富文本描述
            module_desc_richtext = module.get("module_desc_richtext")
            if module_desc_richtext is not None:
                multimedia_id = str(existing.get("multimedia_id", ""))
                try:
                    if multimedia_id:
                        self._course_builder._update_fulltext(
                            multimedia_id, module_desc_richtext, program_id, ref_type=""
                        )
                        logger.info("模块富文本更新成功: module_id=%s, multimedia_id=%s", module_id, multimedia_id)
                    else:
                        new_id = self._course_builder._create_fulltext(
                            module_desc_richtext, ref_type=""
                        )
                        updated["multimedia_type"] = "1"
                        updated["multimedia_id"] = str(new_id)
                        logger.info("模块富文本创建成功: module_id=%s, multimedia_id=%s", module_id, new_id)
                except Exception as e:
                    logger.warning("模块富文本处理失败（非致命）: %s", e)

            new_group_list = module.get("group_list")
            if new_group_list is not None:
                updated["group_list"] = [
                    {
                        "id": str(g.get("id", "")),
                        "group_id": str(g.get("group_id", "")),
                        "order_index": g.get("order_index", idx),
                        "lesson_type": str(g.get("lesson_type", "0")),
                        "is_require": str(g.get("is_require", "1")),
                    }
                    for idx, g in enumerate(new_group_list)
                ]
            else:
                updated["group_list"] = existing.get("group_list", [])

            updated_modules.append(updated)

        data = {
            "program_info": {
                "program_id": str(program_id),
                "program_title": program_info.get("program_title", ""),
                "head_img": program_info.get("head_img", ""),
                "desc": program_info.get("desc", ""),
                "multimedia_type": program_info.get("multimedia_type", 0),
                "multimedia_id": program_info.get("multimedia_id", ""),
                "tags": program_info.get("tags", []),
                "open_module": program_info.get("open_module", "1"),
                "unlock_type": program_info.get("unlock_type", "1"),
                "show_type": program_info.get("show_type", "1"),
                "setup": program_info.get("setup", {}),
            },
            "module_list": updated_modules,
        }

        resp = self.client.post(
            self.client.desktop_url("/api/program/updateinfo"),
            data={
                "program_id": str(program_id),
                "module": "program,module",
                "data": json.dumps(data, ensure_ascii=False),
            },
        )
        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "修改学习项目模块失败"))

        logger.info("学习项目模块修改成功: program_id=%s", program_id)
        return {"program_id": str(program_id)}

    def remove_courses(
        self,
        program_id: str,
        module_group_ids: list[str],
    ) -> dict[str, Any]:
        """从学习项目中删除课程.

        Args:
            program_id: 学习项目 ID
            module_group_ids: 模块课程关系 ID 列表（group_list 中的 id）
        """
        removed: list[str] = []
        failed: list[dict[str, Any]] = []
        for mgid in module_group_ids:
            resp = self.client.post(
                self.client.desktop_url("/api/program/deletegroup"),
                data={"module_group_id": str(mgid)},
            )
            if resp.get("status") in (True, "true") or resp.get("error_code") == 0:
                removed.append(str(mgid))
            else:
                failed.append({"module_group_id": mgid, "reason": resp.get("error", "删除失败")})

        logger.info("学习项目课程删除完成: program_id=%s, removed=%d, failed=%d", program_id, len(removed), len(failed))
        return {"removed": removed, "failed": failed}

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

