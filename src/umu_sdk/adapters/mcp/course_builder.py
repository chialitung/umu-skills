# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""课程构建器 — 封装创建课程和 SCORM 小节的 API 流程.

基于 HAR 分析得到的实际 API 调用序列：
- 创建课程: POST /ajax/e_saveGroup (data=JSON字符串, log=JSON字符串)
- 创建富文本: POST /ajax/multimedia/fulltextadd → POST /ajax/multimedia/fulltextupdcontent
- 创建小节: POST /api/session/savesession (group_id, session_data=JSON字符串)
- 绑定资源: POST /uapi/v2/resource/bind-upd (parent_id, parent_type, resource_data)
- 获取课程: GET /ajax/group/getgroupinfo (group_id)
- 修改课程: POST /ajax/e_saveGroup (data 中 groupInfo 必须包含 id)

Usage:
    from .adapters.mcp.course_builder import CourseBuilder

    builder = CourseBuilder(client)
    course = builder.create_course(title="新课程")
    session = builder.create_scorm_session(
        group_id=course["group_id"],
        session_title="SCORM 小节",
        resource_id="res_xxx",
    )
    info = builder.get_course(group_id="7328938")
    builder.update_course(group_id="7328938", title="新标题")
"""

from __future__ import annotations

import copy
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .image_upload import ImageUploader

logger = logging.getLogger("umu.mcp.teacher.course")

# ---------------------------------------------------------------------------
# 常量定义
# ---------------------------------------------------------------------------

# 只读字段 — 由系统自动填充，修改时不需要提交
_READONLY_GROUPINFO_FIELDS = frozenset({
    "access_code",
    "audit_status",
    "can_assign",
    "can_edit_content_type",
    "creat_time",
    "enterprise_certificate",
    "enrollId",
    "enrollInfo",
    "enrollStatus",
    "finishUserCount",
    "groupId",  # 别名已在 id 中提供
    "id",  # 但作为修改标识需要保留
    "im_rid",
    "im_room_setup",
    "im_url",
    "isCollected",
    "is_assigned",
    "is_course_in_lib",
    "is_creator",
    "is_exist_edit_permission",
    "is_in_trust",
    "is_top",
    "joinUserCount",
    "knowledge_point",
    "lecturerData",
    "lecturing_teacher_add",
    "lecturing_teacher_remove",
    "message_close_status",
    "mini_program_code_url",
    "mini_program_url",
    "outer_obj_status",
    "parent_obj_id",
    "pointSetting",
    "release_detail_desc",
    "release_status",
    "repetitive_course_lock",
    "rewardSessionCount",
    "role",
    "search_text",
    "sessionCountInfo",
    "session_count",
    "session_count_video",
    "session_sort_flag",
    "sharePcUrl",
    "shareQrc",
    "shareUrl",
    "share_card_view",
    "source",
    "totalAmount",
    "update_time",
    "useStatus",
    "vote_count",
    "vote_hide",
    "weikeStat",
    "weike_count",
    "weike_duration_secound",
    "weike_hide",
})

# sessionInfo 中编辑时应过滤的只读字段 — 防止覆盖统计数据和动态生成内容
_READONLY_SESSIONINFO_FIELDS = frozenset({
    "weikeStat",
    "liveStat",
    "totalStat",
    "photoStat",
    "onlineUserCount",
    "totalUserCount",
    "shareQrc",
    "miniProgramQrc",
    "resultUrl",
    "resultQrc",
    "shareUrl",
    "share_card_view",
    "result_card_view",
    "sessionInUse",
    "like_num",
    "market_duplicate_info",
    "enable_learning_tracker",
    "is_cooperation",
})

# groupInfo 中同时存在驼峰和下划线命名的字段映射
# 主键 -> 需要同步更新的别名
_FIELD_ALIASES = {
    "groupTitle": ["title"],
    "groupRemark": ["remark"],
    "headImg": ["head_img"],
    "teacherId": ["teacher_id"],
    "maxOnlineUser": ["max_online_user"],
    "maxUserCount": ["max_user_count"],
    "groupId": ["id"],
}

# 用户参数 -> groupInfo 字段映射
_USER_PARAM_MAP = {
    "title": "groupTitle",
    "desc": "desc",
    "remark": "groupRemark",
    "lesson_type": "lesson_type",
    "other_lesson_type": "other_lesson_type",
    "content_type": "content_type",
    "other_content_type": "other_content_type",
    "course_type": "courseType",
    "event_type": "eventType",
    "province": "province",
    "city": "city",
    "town": "town",
    "address": "address",
    "contact": "contact",
    "contact_phone": "contactPhone",
    "customer_name": "customerName",
    "course_person": "coursePerson",
    "max_online_user": "maxOnlineUser",
    "max_user_count": "maxUserCount",
    "is_important": "isimportant",
    "is_lock": "is_lock",
    "is_repetitive_mode": "is_repetitive_mode",
}


class CourseBuilder:
    """课程构建器 — 封装 UMU 讲师端课程管理 API."""

    # 类级别分类缓存: username -> (tree, timestamp)
    _category_cache: dict[str, tuple[list[dict[str, Any]], datetime]] = {}
    _CACHE_TTL = timedelta(minutes=5)
    # 课程修改前快照: group_id -> snapshot_data
    _snapshots: dict[str, dict[str, Any]] = {}
    _MAX_SNAPSHOTS = 10
    # 写操作冷却期（避免连续调用触发 503）
    _last_write_time: float = 0.0
    _WRITE_COOLDOWN: float = 3.0  # 秒

    def __init__(self, client: Any):
        """初始化构建器.

        Args:
            client: UMUClient 实例
        """
        self.client = client

    # ------------------------------------------------------------------
    # 创建空课程
    # ------------------------------------------------------------------

    def create_course(
        self,
        title: str,
        desc_plain: str = "",
        desc_richtext: str = "",
        cover_url: str = "",
        bg_url: str = "",
        category_ids: list[str] | None = None,
        category_names: list[str] | None = None,
        tags: list[str] | None = None,
        start_date: str = "",
        start_time: str = "",
        end_time: str = "",
    ) -> dict[str, Any]:
        """创建空课程.

        对应 HAR 中的 e_saveGroup API 调用。

        Args:
            title: 课程标题
            desc_plain: 纯文本课程介绍
            desc_richtext: 富文本课程介绍（HTML）
            cover_url: 封面图 URL
            bg_url: 背景图 URL
            category_ids: 分类 ID 列表（与 category_names 二选一）
            category_names: 分类名称列表，支持完整路径如
                ["课程系列 > 新能力系列 > 客户思维"]。与 category_ids 同时
                提供时，category_names 优先。
            tags: 标签文本列表
            start_date: 起始日期 YYYY-MM-DD
            start_time: 起始时间 HH:MM
            end_time: 结束时间 HH:MM

        Returns:
            包含 group_id 等课程信息的字典

        Raises:
            RuntimeError: 保存课程失败
        """
        # 1. 创建富文本内容（如有）
        multimedia_id = ""
        if desc_richtext:
            try:
                multimedia_id = self._create_fulltext(desc_richtext)
                logger.info("富文本创建成功: multimedia_id=%s", multimedia_id)
            except Exception as e:
                logger.warning("富文本创建失败（非致命）: %s", e)

        # 2. 构造 groupInfo
        group_info: dict[str, Any] = {
            "groupTitle": title,
            "courseType": "1",       # 1=在线课程
            "eventType": "7",        # 7=课程
            "lesson_type": "0",
            "content_type": "0",
            "type": "1",
            "desc": desc_plain or "",
            "enrollStatus": "0",
            "is_repetitive_mode": "0",
        }

        if multimedia_id:
            group_info["multimedia_id"] = multimedia_id
            group_info["multimedia_type"] = 1

        if cover_url:
            group_info["headImg"] = cover_url

        if bg_url:
            group_info["bg_img"] = bg_url

        # 3. 处理分类：category_names 优先于 category_ids
        final_category_ids = category_ids or []
        if category_names:
            resolved = self.resolve_category_names(category_names)
            final_category_ids = [cat_id for cat_id, _, _ in resolved]
            logger.info("分类名称解析结果: %s", [
                (name, cid) for cid, name, _ in resolved
            ])

        category_arr = [
            {"category_id": str(cid)} for cid in final_category_ids
        ]

        # 4. 构造 tags
        tags_arr = [{"tag": str(tag)} for tag in (tags or [])]

        # 5. 构造 groupTime
        group_time: list[dict[str, Any]] = []
        if start_date and start_time and end_time:
            group_time.append({
                "groupDay": start_date,
                "startTime": start_time,
                "endTime": end_time,
                "unixTime": 0,          # 后端会自动计算
                "isDisabled": False,
            })

        # 6. 构造 setup（课程设置）
        setup: dict[str, Any] = {
            "auto_proceed_next_element": 0,
            "auto_show_review_card": 1,
            "show_finish_trainees": 1,
            "show_session_index": 1,
            "ai_editor_enable": 0,
            "open_certificate": 1,
            "unlock_condition_type": "0",
            "skin_data": {
                "1": {"show_banner": 1},
                "2": {"show_banner": 0},
                "3": {"show_banner": 0},
                "4": {"show_banner": 0},
            },
            "is_emigrated": 0,
            "desc_first_remind": 0,
        }

        # 7. 组装请求数据
        save_data = {
            "groupInfo": group_info,
            "categoryArr": category_arr,
            "tags": tags_arr,
            "groupTime": group_time,
            "setup": setup,
        }

        log_data: dict[str, Any] = {
            "action": "save",
            "type": "group",
        }

        # 8. 等待冷却期，然后发送请求（带重试）
        self._write_cooldown()
        resp = self._post_with_retry(
            "/ajax/e_saveGroup",
            data={
                "data": json.dumps(save_data, ensure_ascii=False),
                "log": json.dumps(log_data, ensure_ascii=False),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(
                f"保存课程失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        result_data = resp.get("data", {})
        # groupId 嵌套在 data.groupInfo.groupInfo 中
        group_info_wrapper = result_data.get("groupInfo", {})
        group_info_inner = group_info_wrapper.get("groupInfo", {})
        group_id = str(group_info_inner.get("groupId", ""))

        if not group_id:
            raise RuntimeError("保存课程成功但返回的 group_id 为空")

        self._mark_write()
        logger.info("课程创建成功: group_id=%s", group_id)

        # 返回完整课程详情（含 access_code、s_key、share_url 等）
        return self.get_course(group_id)

    # ------------------------------------------------------------------
    # 获取课程信息
    # ------------------------------------------------------------------

    def get_course(self, group_id: str, include_fulltext: bool = False) -> dict[str, Any]:
        """获取课程完整信息.

        调用 GET /ajax/group/getgroupinfo 获取课程详情，过滤掉只读字段，
        返回结构化的可修改字段信息。

        Args:
            group_id: 课程 ID
            include_fulltext: 是否同时获取富文本 HTML 内容

        Returns:
            包含课程可修改字段的字典
        """
        resp = self.client.get(
            self.client.desktop_url("/ajax/group/getgroupinfo"),
            params={"group_id": group_id},
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(
                f"获取课程信息失败: {resp.get('errMsg', resp.get('error', 'unknown'))}"
            )

        info = resp.get("data", {}).get("info", {})
        group_info = info.get("groupInfo", {})

        # 提取 categoryArr（在 info 顶层，嵌套结构）
        category_arr = info.get("categoryArr", [])
        category_ids = self._extract_leaf_category_ids(category_arr)

        # 提取 tags（在 groupInfo 中）
        tags = group_info.get("tags", [])
        tag_names = [t.get("tag", "") if isinstance(t, dict) else str(t) for t in tags]

        # 提取 setup
        setup = group_info.get("setup", {})

        # 提取 groupTime
        group_time = group_info.get("groupTime", [])

        # 构建结果 — 过滤只读字段，保留可修改字段
        result: dict[str, Any] = {
            "group_id": group_id,
            "title": group_info.get("groupTitle") or group_info.get("title", ""),
            "desc": group_info.get("desc", ""),
            "remark": group_info.get("groupRemark") or group_info.get("remark", ""),
            "lesson_type": group_info.get("lesson_type", 0),
            "other_lesson_type": group_info.get("other_lesson_type", ""),
            "content_type": group_info.get("content_type", "0"),
            "other_content_type": group_info.get("other_content_type", ""),
            "course_type": group_info.get("courseType", "1"),
            "event_type": group_info.get("eventType", "7"),
            "head_img": group_info.get("headImg") or group_info.get("head_img", ""),
            "bg_img": group_info.get("bg_img", ""),
            "custom_head_img": group_info.get("custom_head_img", False),
            "multimedia_id": group_info.get("multimedia_id", ""),
            "multimedia_type": group_info.get("multimedia_type", ""),
            "category_ids": category_ids,
            "tags": tag_names,
            "province": group_info.get("province", ""),
            "city": group_info.get("city", ""),
            "town": group_info.get("town", ""),
            "address": group_info.get("address", ""),
            "contact": group_info.get("contact", ""),
            "contact_phone": group_info.get("contactPhone", ""),
            "customer_name": group_info.get("customerName", ""),
            "course_person": group_info.get("coursePerson", ""),
            "max_online_user": group_info.get("maxOnlineUser") or group_info.get("max_online_user", ""),
            "max_user_count": group_info.get("maxUserCount") or group_info.get("max_user_count", ""),
            "is_important": group_info.get("isimportant", "0"),
            "is_lock": group_info.get("is_lock", "0"),
            "is_repetitive_mode": group_info.get("is_repetitive_mode", "0"),
            "stime": group_info.get("stime", 0),
            "etime": group_info.get("etime", 0),
            "start_time": group_info.get("startTime", ""),
            "end_time": group_info.get("endTime", ""),
            "group_time": group_time,
            "setup": setup,
            "enroll_status": group_info.get("enrollStatus", 0),
            "release_status": group_info.get("release_status", "0"),
            "audit_status": group_info.get("audit_status", "0"),
            "access_code": group_info.get("access_code", ""),
        }

        # 获取富文本内容
        if include_fulltext and result["multimedia_id"]:
            try:
                ft_resp = self.client.get(
                    self.client.desktop_url("/ajax/multimedia/fulltextinfo"),
                    params={"top_section_id": result["multimedia_id"]},
                )
                if ft_resp.get("status") is True:
                    result["desc_richtext"] = ft_resp.get("data", {}).get("content", "")
            except Exception as e:
                logger.warning("获取富文本内容失败: %s", e)

        return result

    # ------------------------------------------------------------------
    # 获取课程完整详情（含小节列表和资源删除状态检测）
    # ------------------------------------------------------------------

    def get_course_detail(
        self,
        group_id: str,
        include_fulltext: bool = False,
        check_resource_status: bool = True,
    ) -> dict[str, Any]:
        """获取课程完整详情，包含小节列表和资源删除状态检测.

        聚合 getgroupinfo + getsessionlistbygroup 的数据，返回课程全貌：
        - 课程基本信息（同 get_course）
        - 小节列表（含类型、规则、资源绑定）
        - 每个小节绑定资源的删除状态（通过 resource_info.is_recycle 检测）

        Args:
            group_id: 课程 ID
            include_fulltext: 是否同时获取富文本 HTML 内容
            check_resource_status: 是否检测每个小节绑定资源的删除状态

        Returns:
            包含以下键的字典：
            - course_info: 课程基本信息（同 get_course 返回值）
            - sections: 小节列表，每个元素包含：
                - session_id, title, type, session_type
                - is_required, status, index
                - resource_id: 绑定的资源 ID
                - resource_type: "video"(SCORM/H5) 或 "document"
                - cover_resource_id: 封面资源 ID
                - resource_status: 资源状态字典
                    - id: 资源内部 ID
                    - status: "in_use" 等
                    - is_recycle: "1" 表示在回收站中（资源已被删除）
                    - is_deleted: "1" 表示已彻底删除
                - is_resource_deleted: bool，资源是否被删除
                - rules: 小节规则（setup 的摘要）
                - desc: 小节描述
        """
        # 1. 获取课程基本信息
        course_info = self.get_course(group_id, include_fulltext=include_fulltext)

        # 2. 获取小节完整列表
        resp = self.client.get(
            self.client.desktop_url("/ajax/session/getsessionlistbygroup"),
            params={
                "group_id": group_id,
                "isFirstLoad": "true",
                "is_contain_chapter": 1,
                "page": 1,
                "size": 100,
                "status_str": "0,1",
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(
                f"获取小节列表失败: {resp.get('errMsg', resp.get('error', 'unknown'))}"
            )

        raw_list = resp.get("data", {}).get("list", [])
        sections: list[dict[str, Any]] = []

        # 小节类型映射
        type_map = {
            "11": "scorm",
            "14": "document",
            "15": "infographic",
        }

        for item in raw_list:
            sinfo = item.get("sessionInfo", {})
            section_arr = item.get("sectionArr", [])
            resource_info = item.get("resource_info", {})

            session_id = str(sinfo.get("sessionId", ""))
            session_type = str(sinfo.get("sessionType", ""))
            section_type = type_map.get(session_type, session_type)

            # 提取资源绑定信息
            resource_id = ""
            resource_type = ""
            cover_resource_id = ""
            if section_arr:
                extend = section_arr[0].get("questionInfo", {}).get("extend", {})
                # SCORM/H5 使用 resource_video_id，文档使用 resource_id
                video_id = extend.get("resource_video_id", "")
                doc_id = extend.get("resource_id", "")
                if video_id:
                    resource_id = video_id
                    resource_type = "video"
                elif doc_id:
                    resource_id = doc_id
                    resource_type = "document"
                cover_resource_id = extend.get("custom_cover_resource_id", "")

            # 检测资源删除状态
            is_resource_deleted = False
            resource_status: dict[str, Any] = {}
            if check_resource_status and resource_info:
                resource_status = {
                    "id": resource_info.get("id", ""),
                    "status": resource_info.get("status", ""),
                    "is_recycle": resource_info.get("is_recycle", ""),
                    "is_deleted": resource_info.get("is_deleted", ""),
                }
                # is_recycle="1" 表示资源已被删除到回收站
                is_resource_deleted = resource_info.get("is_recycle") == "1"

            # 提取小节规则摘要
            setup = sinfo.get("setup", {})
            rules_summary: dict[str, Any] = {}
            if section_type == "document":
                # 文档小节规则
                vlt_min = setup.get("vlt_min") or setup.get("vltMin", 0)
                vlt_max = setup.get("vlt_max") or setup.get("vltMax", 0)
                doc_condition = setup.get("document_finished_condition") or setup.get("documentFinishedCondition", "")
                rules_summary = {
                    "vlt_min": vlt_min,
                    "vlt_max": vlt_max,
                    "document_finished_condition": doc_condition,
                    "is_allow_download": setup.get("is_allow_download") or setup.get("isAllowDownload", "0"),
                    "type_name": setup.get("type_name") or setup.get("typeName", ""),
                }
            elif section_type == "scorm":
                # SCORM/视频 小节规则
                rules_summary = {
                    "content_type": setup.get("content_type") or setup.get("contentType", ""),
                    "type_name": setup.get("type_name") or setup.get("typeName", ""),
                    "allow_drag_track": setup.get("allow_drag_track") or setup.get("allowDragTrack", "0"),
                    "allow_adjust_speed": setup.get("allow_adjust_speed") or setup.get("allowAdjustSpeed", "1"),
                    "vlt_min": setup.get("vlt_min") or setup.get("vltMin", 0),
                    "vlt_max": setup.get("vlt_max") or setup.get("vltMax", 0),
                    "desc_first_remind": setup.get("desc_first_remind") or setup.get("descFirstRemind", 0),
                    "is_allow_download": setup.get("is_allow_download") or setup.get("isAllowDownload", "0"),
                }

            sections.append({
                "session_id": session_id,
                "title": sinfo.get("sessionTitle", ""),
                "type": section_type,
                "session_type": session_type,
                "is_required": bool(sinfo.get("is_require", 0)),
                "status": sinfo.get("status", ""),
                "index": sinfo.get("sessionIndex", ""),
                "desc": sinfo.get("desc", ""),
                "resource_id": resource_id,
                "resource_type": resource_type,
                "cover_resource_id": cover_resource_id or None,
                "resource_status": resource_status,
                "is_resource_deleted": is_resource_deleted,
                "rules": rules_summary,
                "tags": [t.get("tag", "") for t in sinfo.get("tags", []) if isinstance(t, dict)],
                "chapter_id": sinfo.get("chapter_id", ""),
                "creat_time": sinfo.get("creatTimeShow", ""),
            })

        return {
            "course_info": course_info,
            "sections": sections,
            "section_count": len(sections),
            "deleted_resource_count": sum(1 for s in sections if s["is_resource_deleted"]),
        }

    # ------------------------------------------------------------------
    # 修改课程信息
    # ------------------------------------------------------------------

    def update_course(
        self,
        group_id: str,
        title: str | None = None,
        desc: str | None = None,
        remark: str | None = None,
        lesson_type: int | None = None,
        other_lesson_type: str | None = None,
        content_type: str | None = None,
        other_content_type: str | None = None,
        category_ids: list[str] | None = None,
        category_names: list[str] | None = None,
        tags: list[str] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        # 语义化时间参数（优先于 start_time/end_time）
        course_start_date: str | None = None,
        course_end_date: str | None = None,
        session_date: str | None = None,
        session_start_time: str | None = None,
        session_end_time: str | None = None,
        cover_image_path: str | None = None,
        bg_image_path: str | None = None,
        desc_richtext: str | None = None,
        desc_richtext_images: list[str] | None = None,
        province: str | None = None,
        city: str | None = None,
        town: str | None = None,
        address: str | None = None,
        contact: str | None = None,
        contact_phone: str | None = None,
        customer_name: str | None = None,
        course_person: str | None = None,
        max_online_user: str | None = None,
        max_user_count: str | None = None,
        is_important: bool | None = None,
        is_lock: bool | None = None,
        is_repetitive_mode: bool | None = None,
        enroll_status: int | str | None = None,
        setup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """增量修改课程信息.

        只更新传入的参数，未传入的字段保持原值。

        流程：
        1. 获取现有课程完整数据
        2. 用传入参数覆盖对应字段
        3. 处理图片上传（封面/背景）
        4. 处理富文本更新
        5. 提交 e_saveGroup

        Args:
            group_id: 课程 ID
            title: 课程标题
            desc: 纯文本描述
            remark: 备注
            lesson_type: 课程形式（0=线上课程, 1=面授培训, 2=混合式课程, 999=其他）
            other_lesson_type: 自定义课程形式
            content_type: 内容类型
            other_content_type: 自定义内容类型
            category_ids: 分类 ID 列表（与 category_names 二选一）
            category_names: 分类名称或路径列表，如
                ["课程系列 > 新能力系列 > 客户思维"]。与 category_ids 同时
                提供时，category_names 优先。
            tags: 标签列表
            start_time: 开始时间（ISO 8601 格式，如 "2026-06-01T09:00:00"）
            end_time: 结束时间（ISO 8601 格式）
            course_start_date: 课程有效期开始日期（YYYY-MM-DD），语义化别名
            course_end_date: 课程有效期结束日期（YYYY-MM-DD），语义化别名
            session_date: 上课日期（YYYY-MM-DD），用于 groupTime
            session_start_time: 上课开始时间（HH:MM），用于 groupTime
            session_end_time: 上课结束时间（HH:MM），用于 groupTime
            cover_image_path: 封面图本地路径
            bg_image_path: 背景图本地路径
            desc_richtext: 富文本介绍（HTML）
            desc_richtext_images: 富文本中需要上传的本地图片路径列表
            province, city, town, address: 地点信息
            contact, contact_phone, customer_name, course_person: 联系人信息
            max_online_user, max_user_count: 人数限制
            is_important: 是否重要课程
            is_lock: 是否锁定
            is_repetitive_mode: 是否重复模式
            enroll_status: 报名设置，0=不需要报名，1=需要报名
            setup: 课程设置字典（高级用法）

        Returns:
            包含更新结果的字典
        """
        # 0. 前置参数校验 — 避免获取数据后才发现参数非法
        if lesson_type is not None:
            if lesson_type not in (0, 1, 2, 999):
                raise ValueError(
                    f"lesson_type 必须是 0(线上课程)/1(面授培训)/2(混合式课程)/999(其他)，"
                    f"收到: {lesson_type}"
                )

        # 校验时间参数格式
        _time_fields = {
            "start_time": start_time,
            "end_time": end_time,
            "course_start_date": course_start_date,
            "course_end_date": course_end_date,
            "session_date": session_date,
        }
        for field_name, field_val in _time_fields.items():
            if field_val is not None and field_val:
                _valid = False
                for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                    try:
                        datetime.strptime(field_val, fmt)
                        _valid = True
                        break
                    except ValueError:
                        continue
                if not _valid:
                    raise ValueError(
                        f"{field_name} 格式错误: '{field_val}'，"
                        "应为 'YYYY-MM-DD' 或 'YYYY-MM-DDTHH:MM:SS'"
                    )

        # session_start_time / session_end_time 校验 HH:MM
        for field_name, field_val in [("session_start_time", session_start_time), ("session_end_time", session_end_time)]:
            if field_val is not None and field_val:
                try:
                    datetime.strptime(field_val, "%H:%M")
                except ValueError:
                    raise ValueError(
                        f"{field_name} 格式错误: '{field_val}'，应为 'HH:MM'"
                    )
        # 0. 前置参数校验 — 避免获取数据后才发现参数非法
        if lesson_type is not None:
            if lesson_type not in (0, 1, 2, 999):
                raise ValueError(
                    f"lesson_type 必须是 0(线上课程)/1(面授培训)/2(混合式课程)/999(其他)，"
                    f"收到: {lesson_type}"
                )

        if start_time is not None:
            _valid = False
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    datetime.strptime(start_time, fmt)
                    _valid = True
                    break
                except ValueError:
                    continue
            if not _valid:
                raise ValueError(
                    f"start_time 格式错误: '{start_time}'，"
                    "应为 'YYYY-MM-DD' 或 'YYYY-MM-DDTHH:MM:SS'"
                )

        if end_time is not None:
            _valid = False
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    datetime.strptime(end_time, fmt)
                    _valid = True
                    break
                except ValueError:
                    continue
            if not _valid:
                raise ValueError(
                    f"end_time 格式错误: '{end_time}'，"
                    "应为 'YYYY-MM-DD' 或 'YYYY-MM-DDTHH:MM:SS'"
                )

        # 1. 获取现有课程数据
        logger.info("获取课程现有数据: group_id=%s", group_id)
        existing = self.get_course(group_id)

        # 2. 处理图片上传（仅当用户传入新图片时）
        cover_url: str | None = None
        if cover_image_path:
            try:
                uploader = ImageUploader(self.client, self.client.base_url)
                result = uploader.upload(cover_image_path, media_type="picweike")
                cover_url = result.file_url
                logger.info("封面上传成功: %s", cover_url)
            except Exception as e:
                logger.error("封面上传失败: %s", e)
                raise RuntimeError(f"封面上传失败: {e}")

        bg_url: str | None = None
        if bg_image_path:
            try:
                uploader = ImageUploader(self.client, self.client.base_url)
                result = uploader.upload(bg_image_path, media_type="picweike")
                bg_url = result.file_url
                logger.info("背景上传成功: %s", bg_url)
            except Exception as e:
                logger.error("背景上传失败: %s", e)
                raise RuntimeError(f"背景上传失败: {e}")

        # 3. 处理富文本图片上传（如果提供了本地图片路径）
        final_richtext = desc_richtext
        if desc_richtext and desc_richtext_images:
            final_richtext = self._process_richtext_images(
                desc_richtext, desc_richtext_images
            )

        # 4. 处理富文本内容（仅当用户传入 desc_richtext 时）
        multimedia_id: str | None = None
        if final_richtext is not None:
            existing_mm_id = existing.get("multimedia_id", "")
            if existing_mm_id:
                # 更新现有富文本
                try:
                    self._update_fulltext(existing_mm_id, final_richtext, group_id)
                    multimedia_id = existing_mm_id
                    logger.info("富文本更新成功: multimedia_id=%s", multimedia_id)
                except Exception as e:
                    logger.error("富文本更新失败: %s", e)
                    raise RuntimeError(f"富文本更新失败: {e}")
            else:
                # 创建新富文本
                try:
                    multimedia_id = self._create_fulltext(final_richtext)
                    logger.info("富文本创建成功: multimedia_id=%s", multimedia_id)
                except Exception as e:
                    logger.error("富文本创建失败: %s", e)
                    raise RuntimeError(f"富文本创建失败: {e}")

        # 5. 构建 groupInfo — 从现有数据开始，应用变更
        group_info: dict[str, Any] = {}

        # 先填充现有 groupInfo 的所有字段（从 getgroupinfo 原始响应中重新获取完整数据）
        resp = self.client.get(
            self.client.desktop_url("/ajax/group/getgroupinfo"),
            params={"group_id": group_id},
        )
        raw_info = resp.get("data", {}).get("info", {})
        raw_group_info = raw_info.get("groupInfo", {})

        # 复制所有字段（包括只读字段，后端可能校验完整性）
        group_info.update(raw_group_info)

        # 应用用户传入的变更
        changes: dict[str, Any] = {}
        if title is not None:
            changes["groupTitle"] = title
        if desc is not None:
            changes["desc"] = desc
        if remark is not None:
            changes["groupRemark"] = remark
        if lesson_type is not None:
            changes["lesson_type"] = lesson_type
        if other_lesson_type is not None:
            changes["other_lesson_type"] = other_lesson_type
        if content_type is not None:
            changes["content_type"] = content_type
        if other_content_type is not None:
            changes["other_content_type"] = other_content_type
        if cover_url:
            changes["headImg"] = cover_url
            changes["custom_head_img"] = True
        if bg_url:
            changes["bg_img"] = bg_url
        if multimedia_id:
            changes["multimedia_id"] = multimedia_id
            changes["multimedia_type"] = 1
        if province is not None:
            changes["province"] = province
        if city is not None:
            changes["city"] = city
        if town is not None:
            changes["town"] = town
        if address is not None:
            changes["address"] = address
        if contact is not None:
            changes["contact"] = contact
        if contact_phone is not None:
            changes["contactPhone"] = contact_phone
        if customer_name is not None:
            changes["customerName"] = customer_name
        if course_person is not None:
            changes["coursePerson"] = course_person
        if max_online_user is not None:
            changes["maxOnlineUser"] = max_online_user
        if max_user_count is not None:
            changes["maxUserCount"] = max_user_count
        if is_important is not None:
            changes["isimportant"] = "1" if is_important else "0"
        if is_lock is not None:
            changes["is_lock"] = "1" if is_lock else "0"
        if is_repetitive_mode is not None:
            changes["is_repetitive_mode"] = "1" if is_repetitive_mode else "0"
        if enroll_status is not None:
            logger.warning(
                "enroll_status 通过 e_saveGroup 不会持久化，"
                "请使用 CourseBuilder.set_course_enrollment 设置报名"
            )
            changes["enrollStatus"] = str(enroll_status)
        if setup is not None:
            changes["setup"] = setup

        # 处理时间变更
        # 优先使用语义化参数，否则回退到 start_time/end_time
        _start = course_start_date or start_time
        _end = course_end_date or end_time
        if _start is not None or _end is not None:
            changes.update(self._build_time_changes(
                _start or existing.get("start_time", ""),
                _end or existing.get("end_time", ""),
            ))

        # 如果提供了 session 时间参数，覆盖 groupTime
        if session_date is not None or session_start_time is not None or session_end_time is not None:
            # 构建新的 groupTime
            gt_date = session_date or changes.get("startTime") or existing.get("start_time", "")
            gt_start = session_start_time or "09:00"
            gt_end = session_end_time or "09:30"

            if gt_date:
                # 解析日期获取 unixTime
                try:
                    dt = datetime.strptime(gt_date, "%Y-%m-%d")
                    unix_time = int(dt.replace(tzinfo=timezone.utc).timestamp())
                except ValueError:
                    unix_time = 0

                changes["groupTime"] = [{
                    "groupDay": gt_date,
                    "startTime": gt_start,
                    "endTime": gt_end,
                    "unixTime": unix_time,
                    "isDisabled": False,
                }]
                # 同时更新 startTime/endTime 的日期部分
                changes["startTime"] = gt_date
                if course_end_date:
                    changes["endTime"] = course_end_date
                elif end_time:
                    changes["endTime"] = end_time
                else:
                    changes["endTime"] = gt_date

        # 应用变更并同步别名
        for key, value in changes.items():
            group_info[key] = value
            # 同步别名
            if key in _FIELD_ALIASES:
                for alias in _FIELD_ALIASES[key]:
                    group_info[alias] = value

        # 6. 构建 save data 顶层字段
        save_data: dict[str, Any] = {"groupInfo": group_info}

        # categoryArr — category_names 优先于 category_ids
        if category_names is not None:
            resolved = self.resolve_category_names(category_names)
            final_cat_ids = [cat_id for cat_id, _, _ in resolved]
            save_data["categoryArr"] = [
                {"category_id": str(cid)} for cid in final_cat_ids
            ]
            logger.info("分类名称解析结果: %s", [
                (name, cid) for cid, name, _ in resolved
            ])
        elif category_ids is not None:
            save_data["categoryArr"] = [
                {"category_id": str(cid)} for cid in category_ids
            ]
        else:
            # 保留现有分类 — 从嵌套结构中提取叶子节点 ID
            existing_categories = raw_info.get("categoryArr", [])
            existing_leaf_ids = self._extract_leaf_category_ids(existing_categories)
            save_data["categoryArr"] = [
                {"category_id": cid} for cid in existing_leaf_ids
            ]

        # tags
        if tags is not None:
            save_data["tags"] = [{"tag": str(tag)} for tag in tags]
        else:
            existing_tags = raw_group_info.get("tags", [])
            save_data["tags"] = existing_tags

        # groupTime — 如果在时间变更中已构建，使用新的；否则保留现有
        if "groupTime" in changes:
            save_data["groupTime"] = changes["groupTime"]
        else:
            existing_gt = raw_group_info.get("groupTime", [])
            save_data["groupTime"] = existing_gt if existing_gt else []

        # setup — 如果在 changes 中已更新，已在 group_info 中；否则保留现有
        if "setup" not in changes:
            existing_setup = raw_group_info.get("setup", {})
            group_info["setup"] = existing_setup

        # 保留统计/权限字段（后端可能要求完整性）
        for field in [
            "session_count", "weike_count", "vote_count", "poll_count",
            "chapter_count", "weike_hide", "vote_hide", "weike_duration_secound",
        ]:
            if field in raw_info and field not in save_data:
                save_data[field] = raw_info[field]

        # 7. 提交保存
        log_data = {"action": "save", "type": "group"}

        # 等待冷却期，然后提交
        self._write_cooldown()
        logger.info("提交课程修改: group_id=%s, 变更字段=%s", group_id, list(changes.keys()))

        resp = self._post_with_retry(
            "/ajax/e_saveGroup",
            data={
                "data": json.dumps(save_data, ensure_ascii=False, default=str),
                "log": json.dumps(log_data, ensure_ascii=False),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            err_msg = resp.get("errMsg") or resp.get("error", "unknown")
            raise RuntimeError(f"保存课程失败: {err_msg}")

        self._mark_write()
        logger.info("课程修改成功: group_id=%s", group_id)

        # 返回完整课程详情（含 access_code、s_key、share_url 等）
        updated_info = self.get_course(group_id)
        updated_info["changes"] = list(changes.keys())
        return updated_info

    # ------------------------------------------------------------------
    # 设置课程报名
    # ------------------------------------------------------------------

    # 默认报名联系信息字段（与 UMU 后台默认保持一致）
    _DEFAULT_ENROLL_CONTACT_INFO: list[dict[str, Any]] = [
        {"key": "username", "questionTitle": "姓名", "defaultPlaceHolder": "输入真实姓名", "placeHolder": "输入真实姓名", "domType": "text"},
        {"key": "mobile", "questionTitle": "手机号", "defaultPlaceHolder": "输入手机号码", "placeHolder": "输入手机号码", "domType": "text"},
        {"key": "company", "questionTitle": "公司", "defaultPlaceHolder": "您的公司", "placeHolder": "您的公司", "domType": "text"},
        {"key": "department", "questionTitle": "部门", "defaultPlaceHolder": "您的部门", "placeHolder": "", "domType": "text"},
        {"key": "position", "questionTitle": "职位", "defaultPlaceHolder": "您的职位", "placeHolder": "", "domType": "text"},
        {"key": "job_number", "questionTitle": "员工号", "defaultPlaceHolder": "您的工号", "placeHolder": "", "domType": "text"},
        {"key": "city", "questionTitle": "城市", "defaultPlaceHolder": "您所在的城市", "placeHolder": "", "domType": "text"},
        {"key": "address", "questionTitle": "地址", "defaultPlaceHolder": "请输入您的地址", "placeHolder": "", "domType": "text"},
        {"key": "sex", "questionTitle": "性别", "defaultPlaceHolder": "", "placeHolder": "", "domType": "radio",
         "questionDefaultValue": [{"value": "2", "text": "女"}, {"value": "1", "text": "男"}]},
        {"key": "email", "questionTitle": "邮箱", "defaultPlaceHolder": "请输入您的邮箱", "placeHolder": "", "domType": "text"},
        {"key": "phone", "questionTitle": "电话号码", "defaultPlaceHolder": "请输入您的电话", "placeHolder": "", "domType": "text"},
        {"key": "qq", "questionTitle": "QQ", "defaultPlaceHolder": "请输入您的QQ", "placeHolder": "", "domType": "text"},
        {"key": "weixin", "questionTitle": "微信", "defaultPlaceHolder": "请输入您的微信", "placeHolder": "", "domType": "text"},
        {"key": "remark", "questionTitle": "备注", "defaultPlaceHolder": "请输入备注", "placeHolder": "", "domType": "text"},
    ]

    def _get_enrollment_base_info(self, group_id: str) -> dict[str, Any]:
        """获取报名设置所需课程基础信息.

        通过 /ajax/group/getgroupinfo 获取课程标题、讲师 ID、分享链接等字段，
        用于构造 /api/enroll/saveenroll 请求体。
        """
        resp = self.client.get(
            self.client.desktop_url("/ajax/group/getgroupinfo"),
            params={"group_id": group_id},
        )
        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            err_msg = resp.get("errMsg") or resp.get("error", "unknown")
            raise RuntimeError(f"获取课程报名基础信息失败: {err_msg}")

        info = resp.get("data", {}).get("info", {})
        group_info = info.get("groupInfo", {})
        return {
            "title": group_info.get("groupTitle") or group_info.get("title", ""),
            "teacher_id": str(group_info.get("teacher_id", "")),
            "share_url": group_info.get("shareUrl", ""),
            "share_qrc": group_info.get("shareQrc", ""),
        }

    def get_course_enrollment(self, group_id: str) -> dict[str, Any]:
        """获取课程当前报名配置.

        调用 /uapi/v1/course/enroll-info。该接口返回的 setup 字段是合并结构
        （含 share/payment 子对象），本方法将其转换为与 saveenroll payload
        对齐的格式：setup 只保留限额/时间/开关等字段，share/payment 拆到
        setupInfo 中。
        """
        resp = self.client.get(
            self.client.desktop_url("/uapi/v1/course/enroll-info"),
            params={"group_id": group_id},
        )
        if resp.get("error_code") != 0 and resp.get("status") not in (True, "true"):
            err_msg = resp.get("error_message") or resp.get("error", "unknown")
            raise RuntimeError(f"获取报名配置失败: {err_msg}")

        data = resp.get("data", {})
        setup = data.get("setup", {})

        # 从 setup 中拆分 share/payment 到 setupInfo，其余字段留在 setup
        setup_info = {
            "share": setup.get(
                "share",
                {
                    "shareStatus": 1,
                    "shareStart": "",
                    "shareEnd": "",
                    "wxShareTitle": "",
                    "wxShareDesc": "",
                },
            ),
            "payment": setup.get("payment", {"switch_status": "0", "amount": "0"}),
        }
        setup_top = {k: v for k, v in setup.items() if k not in ("share", "payment")}

        return {
            "enroll_id": str(data.get("enroll_id", "")),
            "group_id": str(group_id),
            "obj_id": str(group_id),
            "obj_type": "1",
            "teacher_id": str(data.get("teacher_id", "")),
            "title": data.get("title", ""),
            "status": str(data.get("status", "1")),
            "source_mark": str(data.get("source_mark", "1")),
            "multimedia_id": str(data.get("multimedia_id", "0")),
            "multimedia_type": data.get("multimedia_type", 0),
            "create_time": data.get("create_time", ""),
            "update_time": data.get("create_time", ""),
            "shareUrl": data.get("share_url", ""),
            "shareQrc": data.get("share_qrc", ""),
            "totalUserCount": data.get("participate_num", 0),
            "count": [0, 0, 0, 0, 0, 0],
            "inUse": False,
            "autoCheck": str(data.get("auto_check", "1")),
            "desc": data.get("desc", ""),
            "payment": {"switch_status": 0, "amount": 0},
            "totalAmount": "0",
            "sectionArr": data.get("sectionArr", []),
            "contactInfo": data.get("contactInfo", []),
            "setupInfo": setup_info,
            "setup": setup_top,
        }

    def set_course_enrollment(
        self,
        group_id: str,
        enabled: bool = True,
        auto_check: bool = True,
        title: str | None = None,
        desc: str = "",
        contact_info: list[dict[str, Any]] | None = None,
        selected_contact_fields: list[str] | None = None,
        allow_cancel: bool = False,
        user_quota: int = -1,
        begin_time: int | str = 0,
        end_time: int | str = 0,
        price_amount: int = 0,
        section_questions: list[dict[str, Any]] | None = None,
        approval_setting: dict[str, Any] | None = None,
        enroll_id: str = "",
    ) -> dict[str, Any]:
        """设置课程报名开关及报名信息.

        UMU 的报名开关不通过 e_saveGroup 持久化，必须调用独立的
        /api/enroll/saveenroll 接口。

        当传入 enroll_id 时，会先调用 /uapi/v1/course/enroll-info 读取现有报名
        配置，再与用户传入的参数做增量合并，保留 create_time 等未变更字段。

        Args:
            group_id: 课程 ID
            enabled: True=开启报名，False=关闭报名
            auto_check: 是否自动审核报名（默认 True）
            title: 报名标题，默认使用课程标题
            desc: 报名说明
            contact_info: 自定义报名联系信息字段，None 使用默认字段。
                每个字段可包含 isSelected/isRequired 来控制是否勾选与必填。
            selected_contact_fields: 快速指定要勾选的默认字段 key 列表
                （如 ["username", "mobile"]），与 contact_info 可叠加。
            allow_cancel: 是否允许学员取消报名
            user_quota: 报名人数上限，-1 表示不限制
            begin_time: 报名开始时间（Unix 时间戳或 0 表示不限制）
            end_time: 报名结束时间（Unix 时间戳或 0 表示不限制）
            price_amount: 报名价格（分），0 表示免费
            section_questions: 自定义报名问题列表，每个问题支持 title、type、
                required、options 等字段。type 支持 textarea/radio/checkbox/
                paragraph/number，简写 text 会被映射为 textarea。
            approval_setting: 审核人设置，格式
                {"course_manager": bool, "department_manager": bool, "designees": [...]}
            enroll_id: 现有报名 ID（修改时传入，新建留空）

        Returns:
            包含 enroll_id 等字段的字典

        Raises:
            RuntimeError: 设置失败
        """
        if not group_id:
            raise ValueError("group_id 不能为空")

        # 获取课程基础信息（标题、讲师 ID、分享链接等）
        base_info = self._get_enrollment_base_info(group_id)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 修改报名时先加载现有配置，作为增量合并的基底
        existing: dict[str, Any] | None = None
        if enroll_id:
            try:
                existing = self.get_course_enrollment(group_id)
                # 若传入的 enroll_id 与接口返回不一致，以传入为准
                existing["enrollId"] = str(enroll_id)
            except Exception as e:
                logger.warning("读取现有报名配置失败，将使用新建逻辑: %s", e)
                existing = None

        if existing:
            payload = copy.deepcopy(existing)
        else:
            payload = {
                "group_id": str(group_id),
                "obj_id": str(group_id),
                "obj_type": "1",
                "teacher_id": base_info["teacher_id"],
                "title": base_info["title"],
                "status": "1",
                "source_mark": "1",
                "multimedia_id": "0",
                "multimedia_type": 0,
                "create_time": now_str,
                "shareUrl": base_info.get("share_url", ""),
                "shareQrc": base_info.get("share_qrc", ""),
                "totalUserCount": 0,
                "count": [0, 0, 0, 0, 0, 0],
                "inUse": False,
                "enrollId": str(enroll_id),
                "autoCheck": "1",
                "desc": "",
                "payment": {"switch_status": 0, "amount": 0},
                "totalAmount": "0",
                "sectionArr": [],
                "contactInfo": [],
                "setupInfo": {
                    "share": {
                        "shareStatus": 1,
                        "shareStart": "",
                        "shareEnd": "",
                        "wxShareTitle": "",
                        "wxShareDesc": "",
                    },
                    "payment": {"switch_status": "0", "amount": "0"},
                },
                "setup": {},
            }

        # 应用顶层覆盖
        if title is not None:
            payload["title"] = title
        elif not existing:
            payload["title"] = base_info["title"]

        payload["status"] = "1" if enabled else "0"
        payload["autoCheck"] = "1" if auto_check else "0"
        payload["desc"] = desc
        payment_switch = 1 if price_amount and price_amount > 0 else 0
        payload["payment"] = {"switch_status": payment_switch, "amount": int(price_amount or 0)}
        payload["update_time"] = now_str

        # setup 合并：显式参数覆盖，缺失字段使用 HAR 默认值补齐
        setup_defaults = {
            "switch_status": "0",
            "amount": "0",
            "begin_time": str(begin_time),
            "end_time": str(end_time),
            "user_quota": str(user_quota),
            "allow_upd_enroll_switch": "1",
            "max_user_quota": "-1",
            "allow_reject_participate_user": "1",
            "allow_clear": "1",
            "enable_expiry": "0",
            "expiry_days": "0",
            "allow_cancel": "1" if allow_cancel else "0",
        }
        payload.setdefault("setup", {})
        for key, default_value in setup_defaults.items():
            if key in ("begin_time", "end_time", "user_quota", "allow_cancel"):
                payload["setup"][key] = default_value
            else:
                payload["setup"].setdefault(key, default_value)

        # setupInfo 固定结构，与 HAR 一致，不跟随 price_amount
        payload["setupInfo"] = {
            "share": {
                "shareStatus": 1,
                "shareStart": "",
                "shareEnd": "",
                "wxShareTitle": "",
                "wxShareDesc": "",
            },
            "payment": {"switch_status": "0", "amount": "0"},
        }

        # contactInfo：用户传入则替换，否则保留 existing（新建时为空）
        if contact_info is not None or selected_contact_fields is not None:
            payload["contactInfo"] = self._format_contact_info(
                contact_info, selected_contact_fields
            )
        elif not existing:
            payload["contactInfo"] = self._format_contact_info(None, None)

        # sectionArr：用户传入则按索引合并，保留原有问题/选项 ID
        if section_questions is not None:
            payload["sectionArr"] = self._merge_section_arr(
                payload.get("sectionArr", []), section_questions
            )

        self._write_cooldown()
        logger.info(
            "设置课程报名: group_id=%s, enabled=%s, auto_check=%s, price=%s, sections=%s",
            group_id,
            enabled,
            auto_check,
            price_amount,
            len(payload.get("sectionArr", [])),
        )

        resp = self.client.post(
            self.client.desktop_url("/api/enroll/saveenroll"),
            data={"enroll": json.dumps(payload, ensure_ascii=False)},
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            err_msg = resp.get("errMsg") or resp.get("error", "unknown")
            raise RuntimeError(f"设置课程报名失败: {err_msg}")

        self._mark_write()

        data = resp.get("data", {})
        enroll_id_returned = str(data.get("enrollId", enroll_id))
        logger.info("课程报名设置成功: group_id=%s, enroll_id=%s", group_id, enroll_id_returned)

        # 保存审核人设置
        if approval_setting and enroll_id_returned:
            self._save_enroll_approval_setting(enroll_id_returned, approval_setting)

        return {
            "group_id": group_id,
            "enroll_id": enroll_id_returned,
            "enabled": enabled,
            "auto_check": auto_check,
        }

    def _format_contact_info(
        self,
        contact_info: list[dict[str, Any]] | None,
        selected_contact_fields: list[str] | None,
    ) -> list[dict[str, Any]]:
        """构造报名联系信息字段."""
        fields = contact_info if contact_info is not None else self._DEFAULT_ENROLL_CONTACT_INFO
        selected_keys = set(selected_contact_fields or [])
        formatted = []
        for field in fields:
            default_value = field.get("questionDefaultValue", [{"value": "", "text": ""}])
            is_selected_raw = field.get("isSelected", field.get("selected"))
            if is_selected_raw is None:
                is_selected = field.get("key", "") in selected_keys
            else:
                is_selected = str(is_selected_raw) in ("1", "true", "True", True)
            is_required = str(field.get("isRequired", field.get("required", True))) in (
                "1",
                "true",
                "True",
                True,
            )
            formatted.append({
                "isMustKey": "0",
                "canMove": "1",
                "domType": field.get("domType", "text"),
                "isRequired": "1" if is_required else "0",
                "isSelected": "1" if is_selected else "0",
                "questionTitle": field["questionTitle"],
                "defaultPlaceHolder": field.get("defaultPlaceHolder", ""),
                "placeHolder": field.get("placeHolder", field.get("defaultPlaceHolder", "")),
                "questionDefaultValue": default_value,
                "questionValue": "",
                "key": field["key"],
            })
        return formatted

    def _merge_section_arr(
        self,
        existing_sections: list[dict[str, Any]],
        questions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """将简化问题列表与现有 sectionArr 合并，保留原有问题/选项 ID."""
        result = []
        for idx, q in enumerate(questions, start=1):
            new_section = self._format_section_question(q, idx)
            if idx <= len(existing_sections):
                old = existing_sections[idx - 1]
                old_info = old.get("questionInfo", {})
                new_info = new_section["questionInfo"]
                # 同位置且类型相同则保留原有 ID
                if old_info.get("domType") == new_info.get("domType"):
                    new_info["questionId"] = old_info.get("questionId", "")
                    new_info["sessionId"] = old_info.get("sessionId", "")
                    new_info["enrollId"] = old_info.get("enrollId", "")
                    old_answers = old.get("answerArr", [])
                    new_answers = new_section["answerArr"]
                    for a_idx, new_a in enumerate(new_answers):
                        if a_idx < len(old_answers):
                            new_a["answerId"] = old_answers[a_idx].get("answerId", "")
                            new_a["questionId"] = old_answers[a_idx].get("questionId", "")
            result.append(new_section)
        return result

    def _format_section_question(self, q: dict[str, Any], index: int) -> dict[str, Any]:
        """将简化的报名问题格式转换为 UMU sectionArr 格式.

        参考 HAR/tracer 中真实的 /api/enroll/saveenroll 请求：
        - 选项放在 answerArr 中（radio/checkbox）
        - setup.required 中 "0" 表示必填，"1" 表示选填
        - extend.pic_url 对 radio/checkbox/textarea 固定为 []
        """
        q_type = q.get("type", q.get("domType", "text"))
        # 简化类型 -> UMU domType 映射
        type_map = {"text": "textarea"}
        q_type = type_map.get(q_type, q_type)
        title = q.get("title", q.get("questionTitle", f"问题{index}"))
        required = str(q.get("required", q.get("isRequired", False))) in (
            "1",
            "true",
            "True",
            True,
        )
        options = q.get("options", q.get("questionDefaultValue", []))

        # UMU 约定：setup.required "0"=必填，"1"=选填
        setup: dict[str, Any] = {"required": "0" if required else "1"}
        extend: dict[str, Any] = {"pic_url": []}
        answer_arr: list[dict[str, Any]] = []

        if q_type == "radio":
            answer_arr = [{"answerContent": opt["text"] if isinstance(opt, dict) else str(opt)} for opt in options]
        elif q_type == "checkbox":
            answer_arr = [{"answerContent": opt["text"] if isinstance(opt, dict) else str(opt)} for opt in options]
            min_opts = q.get("min_options", q.get("limitOptionsMin", 0))
            max_opts = q.get("max_options", q.get("limitOptionsMax", 0))
            if min_opts:
                setup["limitOptionsMin"] = int(min_opts)
            if max_opts:
                setup["limitOptionsMax"] = int(max_opts)
        elif q_type == "textarea":
            placeholder = q.get("placeholder", q.get("defaultPlaceHolder", ""))
            if placeholder:
                answer_arr = [{"answerContent": placeholder}]
        elif q_type == "number":
            extend["min"] = q.get("min", 0)
            extend["max"] = q.get("max", 100)
            if q.get("min_desc"):
                extend["minDesc"] = q["min_desc"]
            if q.get("max_desc"):
                extend["maxDesc"] = q["max_desc"]
            setup["defaultValue"] = q.get("default_value", q.get("defaultValue", ""))
        elif q_type == "paragraph":
            extend = {}

        return {
            "questionInfo": {
                "questionId": "",
                "sessionId": "",
                "questionTitle": title,
                "questionIndex": index,
                "pattern": "",
                "required": "",
                "creatTime": "",
                "creatTimeShow": "",
                "domType": q_type,
                "totalCount": "",
                "showType": {},
                "showIndex": index,
                "setup": setup,
                "extend": extend,
                "cid": q.get("cid", ""),
                "desc": q.get("desc", ""),
            },
            "answerArr": answer_arr,
        }

    def _save_enroll_approval_setting(
        self, enroll_id: str, approval_setting: dict[str, Any]
    ) -> dict[str, Any]:
        """保存报名审核人设置."""
        setting = {
            "manager_permission": 1 if approval_setting.get("course_manager", False) else 0,
            "department_manager_permission": 1
            if approval_setting.get("department_manager", False)
            else 0,
            "designee_permission": 1
            if approval_setting.get("designee", False)
            or bool(approval_setting.get("designees"))
            else 0,
        }
        resp = self.client.post(
            self.client.desktop_url("/uapi/v1/enroll/save-approval-setting"),
            data={
                "enroll_id": str(enroll_id),
                "setting": json.dumps(setting, ensure_ascii=False),
            },
        )
        if resp.get("error_code") != 0 and resp.get("status") not in (True, "true"):
            err_msg = resp.get("error_message") or resp.get("error", "unknown")
            raise RuntimeError(f"保存报名审核设置失败: {err_msg}")
        logger.info("报名审核设置保存成功: enroll_id=%s", enroll_id)
        return resp

    # ------------------------------------------------------------------
    # 辅助方法：处理富文本中的本地图片
    # ------------------------------------------------------------------

    def _process_richtext_images(self, html: str, image_paths: list[str]) -> str:
        """将富文本 HTML 中的本地图片路径替换为 COS URL.

        Args:
            html: 富文本 HTML
            image_paths: 本地图片路径列表

        Returns:
            替换后的 HTML
        """

        result = html
        for path in image_paths:
            if not path or path not in html:
                continue
            try:
                uploader = ImageUploader(self.client, self.client.base_url)
                up_result = uploader.upload(path, media_type="imagefulltext")
                # 替换 HTML 中的路径
                result = result.replace(path, up_result.file_url)
                logger.info("富文本图片上传成功: %s -> %s", path, up_result.file_url)
            except Exception as e:
                logger.error("富文本图片上传失败 %s: %s", path, e)
                raise RuntimeError(f"富文本图片上传失败 {path}: {e}")

        return result

    # ------------------------------------------------------------------
    # 辅助方法：构建时间变更
    # ------------------------------------------------------------------

    def _build_time_changes(self, start_time_str: str, end_time_str: str) -> dict[str, Any]:
        """将 ISO 8601 时间字符串转换为 API 所需格式.

        Args:
            start_time_str: 开始时间（如 "2026-06-01T09:00:00" 或 "2026-06-01"）
            end_time_str: 结束时间

        Returns:
            包含 stime, etime, startTime, endTime, groupTime 的字典
        """
        changes: dict[str, Any] = {}

        # 解析开始时间
        start_dt = None
        start_has_time = False
        if start_time_str:
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    start_dt = datetime.strptime(start_time_str, fmt)
                    if "T" in start_time_str:
                        start_has_time = True
                    break
                except ValueError:
                    continue
            if start_dt:
                changes["stime"] = int(start_dt.replace(tzinfo=timezone.utc).timestamp())
                changes["startTime"] = start_dt.strftime("%Y-%m-%d")

        # 解析结束时间
        end_dt = None
        end_has_time = False
        if end_time_str:
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    end_dt = datetime.strptime(end_time_str, fmt)
                    if "T" in end_time_str:
                        end_has_time = True
                    break
                except ValueError:
                    continue
            if end_dt:
                if end_has_time:
                    # 如果传入具体时间，使用该时间戳
                    changes["etime"] = int(end_dt.replace(tzinfo=timezone.utc).timestamp())
                else:
                    # 如果只传入日期，设为当天最后一秒
                    changes["etime"] = int(end_dt.replace(tzinfo=timezone.utc).timestamp()) + 86399
                changes["endTime"] = end_dt.strftime("%Y-%m-%d")

        # 构建 groupTime（标准学时）
        if start_dt:
            group_day = start_dt.strftime("%Y-%m-%d")
            if start_has_time and end_has_time and end_dt:
                start_hm = start_dt.strftime("%H:%M")
                end_hm = end_dt.strftime("%H:%M")
            else:
                start_hm = "09:00"
                end_hm = "09:30"

            changes["groupTime"] = [{
                "groupDay": group_day,
                "startTime": start_hm,
                "endTime": end_hm,
                "unixTime": changes.get("stime", 0),
                "isDisabled": False,
            }]

        return changes

    # ------------------------------------------------------------------
    # 辅助方法：更新富文本内容
    # ------------------------------------------------------------------

    def _update_fulltext(
        self,
        top_section_id: str,
        content: str,
        ref_id: str,
        ref_type: str = "group",
    ) -> None:
        """更新现有富文本内容.

        Args:
            top_section_id: 富文本 ID
            content: HTML 内容
            ref_id: 关联对象 ID
            ref_type: 关联类型，课程描述用 "group"（默认），
                     文章小节内容用 "article"
        """
        resp = self.client.post(
            self.client.desktop_url("/ajax/multimedia/fulltextupdcontent"),
            data={
                "content": content,
                "ref_type": ref_type,
                "ref_id": ref_id,
                "top_section_id": top_section_id,
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(
                f"更新富文本失败: {resp.get('error', 'unknown')}"
            )

    # ------------------------------------------------------------------
    # 创建富文本内容
    # ------------------------------------------------------------------

    def _create_fulltext(
        self, content: str, ref_type: str = "group"
    ) -> str:
        """创建富文本内容，返回 multimedia_id.

        流程：
        POST /ajax/multimedia/fulltextadd 直接创建带内容的富文本
        （在 fulltextadd 中直接传入 content，避免 fulltextupdcontent 超时问题）

        Args:
            content: HTML 内容
            ref_type: 关联类型，课程描述用 "group"（默认），
                     视频小节描述用 ""（空字符串）

        Returns:
            富文本内容 ID (top_section_id)

        Raises:
            RuntimeError: 创建失败
        """
        resp = self.client.post(
            self.client.desktop_url("/ajax/multimedia/fulltextadd"),
            data={
                "content": content,
                "ref_type": ref_type,
                "ref_id": "0",
            },
            timeout=60.0,
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(
                f"创建富文本失败: {resp.get('error', 'unknown')}"
            )

        top_section_id = str(resp.get("data", {}).get("top_section_id", ""))

        if not top_section_id:
            top_section_id = str(resp.get("data", {}).get("id", ""))

        if not top_section_id:
            raise RuntimeError("创建富文本成功但返回的 ID 为空")

        logger.info(
            "富文本创建成功: top_section_id=%s, content_length=%d",
            top_section_id,
            len(content),
        )
        return top_section_id

    # ------------------------------------------------------------------
    # 视频小节公共辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_video_section_params(
        desc_plain: str | None,
        desc_richtext: str | None,
        cover_image_path: str | None,
        cover_resource_id: str | None,
        remove_cover: bool = False,
    ) -> None:
        """校验视频小节参数互斥关系.

        Raises:
            ValueError: 参数冲突
        """
        if desc_plain is not None and desc_richtext is not None:
            raise ValueError(
                "desc_plain 和 desc_richtext 不能同时提供，请二选一"
            )
        if cover_image_path is not None and cover_resource_id is not None:
            raise ValueError(
                "cover_image_path 和 cover_resource_id 不能同时提供，请二选一"
            )
        if remove_cover and (cover_image_path or cover_resource_id):
            raise ValueError(
                "remove_cover 与 cover_image_path/cover_resource_id 不能同时使用"
            )

    def _upload_cover_image(
        self,
        cover_image_path: str | None,
        fatal: bool = False,
    ) -> str:
        """上传封面图并返回 resource_id.

        Args:
            cover_image_path: 本地图片路径
            fatal: 上传失败时是否抛出异常（False=返回空字符串）

        Returns:
            封面图 resource_id，失败返回空字符串

        Raises:
            RuntimeError: fatal=True 且上传失败时
        """
        if not cover_image_path:
            return ""
        try:
            uploader = ImageUploader(self.client, self.client.base_url)
            result = uploader.upload(cover_image_path, media_type="picweike")
            logger.info("封面图上传成功: resource_id=%s", result.resource_id)
            return result.resource_id
        except Exception as e:
            if fatal:
                raise RuntimeError(f"上传封面图失败: {e}")
            logger.warning("封面图上传失败（非致命）: %s", e)
            return ""

    def _process_video_description(
        self,
        desc_plain: str | None,
        desc_richtext: str | None,
        current_multimedia_type: int = 0,
        current_multimedia_id: int = 0,
        current_desc: str = "",
        fatal_richtext: bool = False,
    ) -> tuple[int, int | str, str]:
        """处理视频说明（纯文本或富文本），返回 (multimedia_type, multimedia_id, desc).

        Args:
            desc_plain: 纯文本说明（None=不修改/不设置）
            desc_richtext: 富文本 HTML（None=不修改/不设置）
            current_multimedia_type: 当前 multimedia_type（修改时传入现有值）
            current_multimedia_id: 当前 multimedia_id（修改时传入现有值）
            current_desc: 当前 desc（修改时传入现有值）
            fatal_richtext: 富文本创建失败时是否抛出异常

        Returns:
            (multimedia_type, multimedia_id, desc)
        """
        if desc_richtext is not None:
            try:
                multimedia_id = self._create_fulltext(desc_richtext, ref_type="")
                logger.info("视频说明富文本创建成功: multimedia_id=%s", multimedia_id)
                return 1, multimedia_id, ""
            except Exception as e:
                if fatal_richtext:
                    raise RuntimeError(f"创建视频说明富文本失败: {e}")
                logger.warning("视频说明富文本创建失败（非致命）: %s", e)
                return current_multimedia_type, current_multimedia_id, current_desc

        if desc_plain is not None:
            return 0, 0, desc_plain

        # 未提供任何说明参数，保持当前值
        return current_multimedia_type, current_multimedia_id, current_desc

    @staticmethod
    def _build_video_setup(
        allow_drag_track: bool | None = None,
        allow_adjust_speed: bool | None = None,
        min_duration_seconds: int | None = None,
        max_duration_seconds: int | None = None,
        desc_first_remind: bool | None = None,
    ) -> dict[str, Any]:
        """构造视频小节 setup 字典.

        只包含传入非 None 的参数，便于增量更新。
        所有布尔参数统一转换为整数（0/1）。

        Returns:
            setup 字典（可能为空）
        """
        setup: dict[str, Any] = {}
        if allow_drag_track is not None:
            setup["allow_drag_track"] = 1 if allow_drag_track else 0
        if allow_adjust_speed is not None:
            setup["allow_adjust_speed"] = 1 if allow_adjust_speed else 0
        if min_duration_seconds is not None:
            setup["vlt_min"] = min_duration_seconds
        if max_duration_seconds is not None:
            setup["vlt_max"] = max_duration_seconds
        if desc_first_remind is not None:
            setup["desc_first_remind"] = 1 if desc_first_remind else 0
        return setup

    @staticmethod
    def _build_question_info(extend: dict[str, Any]) -> dict[str, Any]:
        """构造 questionInfo 字典（用于 sectionArr）.

        Args:
            extend: extend 字典（包含 resource_video_id、cover_index 等）

        Returns:
            questionInfo 字典
        """
        return {
            "questionId": "",
            "sessionId": "",
            "questionTitle": "",
            "questionIndex": "",
            "pattern": "",
            "required": "",
            "creatTime": "",
            "creatTimeShow": "",
            "domType": "weike",
            "totalCount": "",
            "showType": {},
            "showIndex": 0,
            "setup": {},
            "extend": extend,
        }

    @staticmethod
    def _build_session_data(
        session_info: dict[str, Any],
        question_info: dict[str, Any],
    ) -> dict[str, Any]:
        """构造 session_data 字典（用于 savesession）.

        Args:
            session_info: sessionInfo 字典
            question_info: questionInfo 字典

        Returns:
            session_data 字典
        """
        return {
            "sessionInfo": session_info,
            "sectionArr": [
                {
                    "questionInfo": question_info,
                    "answerArr": [],
                }
            ],
        }

    @staticmethod
    def _filter_readonly_fields(session_info: dict[str, Any]) -> None:
        """从 sessionInfo 中过滤只读字段（原地修改）.

        Args:
            session_info: sessionInfo 字典（会被原地修改）
        """
        for field in CourseBuilder._SESSION_READONLY_FIELDS:
            session_info.pop(field, None)
        setup = session_info.get("setup", {})
        if isinstance(setup, dict):
            for field in CourseBuilder._SETUP_READONLY_FIELDS:
                setup.pop(field, None)

    def _bind_resources_to_session(
        self,
        session_id: str,
        video_resource_id: str = "",
        cover_resource_id: str = "",
        old_video_resource_id: str = "",
        old_cover_resource_id: str = "",
    ) -> None:
        """统一资源绑定方法（支持解绑旧资源）.

        Args:
            session_id: 小节 ID
            video_resource_id: 新视频资源 ID（空字符串=不绑定新视频）
            cover_resource_id: 新封面资源 ID（空字符串=不绑定新封面）
            old_video_resource_id: 旧视频资源 ID（用于解绑）
            old_cover_resource_id: 旧封面资源 ID（用于解绑）

        Raises:
            RuntimeError: 绑定失败
        """
        resource_data: list[dict[str, Any]] = []

        # 视频资源绑定/解绑
        if video_resource_id and video_resource_id != old_video_resource_id:
            resource_data.append({
                "resource_type": 1,
                "bind_resource_ids": [video_resource_id],
                "unbind_resource_ids": (
                    [old_video_resource_id] if old_video_resource_id else []
                ),
            })
        elif old_video_resource_id and not video_resource_id:
            # 仅解绑旧视频
            resource_data.append({
                "resource_type": 1,
                "bind_resource_ids": [],
                "unbind_resource_ids": [old_video_resource_id],
            })

        # 封面资源绑定/解绑
        if cover_resource_id and cover_resource_id != old_cover_resource_id:
            resource_data.append({
                "resource_type": 6,
                "bind_resource_ids": [cover_resource_id],
                "unbind_resource_ids": (
                    [old_cover_resource_id] if old_cover_resource_id else []
                ),
            })
        elif old_cover_resource_id and not cover_resource_id:
            # 仅解绑旧封面
            resource_data.append({
                "resource_type": 6,
                "bind_resource_ids": [],
                "unbind_resource_ids": [old_cover_resource_id],
            })

        if not resource_data:
            logger.debug("无需资源绑定变更")
            return

        resp = self.client.post(
            self.client.desktop_url("/uapi/v2/resource/bind-upd"),
            data={
                "parent_id": session_id,
                "parent_type": "4",
                "resource_data": json.dumps(resource_data, ensure_ascii=False),
            },
        )

        if resp.get("error_code") != 0:
            raise RuntimeError(
                f"资源绑定失败: {resp.get('error_message', 'unknown')}"
            )

        logger.info(
            "资源绑定成功: session_id=%s, video=%s, cover=%s",
            session_id,
            video_resource_id or "(no change)",
            cover_resource_id or "(no change)",
        )

    def _save_keywords(
        self,
        session_id: str,
        tags: list[str] | None = None,
    ) -> None:
        """保存小节标签.

        Args:
            session_id: 小节 ID
            tags: 标签列表（None=不保存）
        """
        if tags is None:
            return
        try:
            self.client.post(
                self.client.desktop_url("/uapi/v1/keywords/save"),
                data={
                    "parent_id": session_id,
                    "parent_type": "4",
                    "keywords": json.dumps([], ensure_ascii=False),
                },
            )
            logger.info("标签保存成功: session_id=%s", session_id)
        except Exception as e:
            logger.warning("标签保存失败（非致命）: %s", e)

    # ------------------------------------------------------------------
    # 创建 SCORM 小节
    # ------------------------------------------------------------------

    def create_scorm_session(
        self,
        group_id: str,
        session_title: str,
        resource_id: str,
        cover_resource_id: str = "",
        duration_minutes: int = 0,
        is_required: bool = True,
        sort_order: int = 0,
    ) -> dict[str, Any]:
        """在课程中创建 SCORM 类型小节（sessionType=11，与视频微课共用码值）并绑定资源.

        采用两步法：
        1. 调用 savesession 创建空小节（不带 resource，避免 Server Error）
        2. 调用 bind-upd 绑定 SCORM 资源

        Args:
            group_id: 课程 ID
            session_title: 小节标题
            resource_id: SCORM 资源 ID
            cover_resource_id: 小节封面图资源 ID（可选）
            duration_minutes: 预计学习时长（分钟）
            is_required: 是否必修
            sort_order: 排序序号，0 表示自动追加

        Returns:
            包含 session_id 等信息的字典

        Raises:
            RuntimeError: 创建小节失败
        """
        # 1. 构造 session_data
        # 参照 HAR：sectionArr 中预置 questionInfo.extend，前端依赖此字段显示封面
        setup: dict[str, Any] = {
            "content_type": "scorm",
            "type_name": "H5",
            "allow_drag_track": "0",
            "allow_adjust_speed": 1,
            "is_allow_download": 0,
            "ai_session_summary_switch": "0",
            "enable_ai_subtitles": "0",
            "close_comment_switch": 0,
            "is_comment_time_visible": "1",
        }

        question_info: dict[str, Any] = {
            "questionId": "",
            "sessionId": "",
            "questionTitle": "",
            "questionIndex": "",
            "pattern": "",
            "required": "",
            "creatTime": "",
            "creatTimeShow": "",
            "domType": "weike",
            "totalCount": "",
            "showType": {},
            "showIndex": 0,
            "setup": {},
            "extend": {
                "resource_video_id": resource_id,
                "cover_index": 999,
            },
        }
        if cover_resource_id:
            question_info["extend"]["custom_cover_resource_id"] = cover_resource_id

        session_data = {
            "sessionInfo": {
                "sessionTitle": session_title,
                "sessionType": "11",
                "multimedia_type": 1,
                "multimedia_id": 0,
                "setup": setup,
                "point_ratio": "1",
                "is_require": "1" if is_required else "0",
                "tags": [],
            },
            "sectionArr": [
                {
                    "questionInfo": question_info,
                    "answerArr": [],
                }
            ],
        }

        # 2. 调用 savesession 创建空小节
        resp = self.client.post(
            self.client.desktop_url("/api/session/savesession"),
            data={
                "group_id": group_id,
                "session_data": json.dumps(session_data, ensure_ascii=False),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error(
                "savesession 错误响应: %s",
                json.dumps(resp, ensure_ascii=False),
            )
            raise RuntimeError(
                f"保存小节失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        result_data = resp.get("data", {})
        session_id = str(result_data.get("session_id", ""))

        if not session_id:
            raise RuntimeError("保存小节成功但返回的 session_id 为空")

        logger.info(
            "小节创建成功: session_id=%s, group_id=%s",
            session_id,
            group_id,
        )

        # 3. 调用 bind-upd 绑定资源
        try:
            self._bind_resources_to_session(
                session_id=session_id,
                video_resource_id=resource_id,
                cover_resource_id=cover_resource_id,
            )
        except Exception as e:
            logger.error("资源绑定失败: %s", e)
            raise RuntimeError(f"小节已创建但资源绑定失败: {e}")

        return {
            "session_id": session_id,
            "group_id": group_id,
            "title": session_title,
            "resource_id": resource_id,
        }

    # ------------------------------------------------------------------
    # 创建视频小节
    # ------------------------------------------------------------------

    def create_video_section(
        self,
        group_id: str,
        session_title: str,
        video_resource_id: str,
        cover_image_path: str = "",
        cover_resource_id: str = "",
        desc_plain: str = "",
        desc_richtext: str = "",
        is_required: bool = True,
        allow_drag_track: bool = True,
        allow_adjust_speed: bool = True,
        min_duration_seconds: int = 0,
        max_duration_seconds: int = 0,
        desc_first_remind: bool = False,
        tags: list[str] | None = None,
        sort_order: int = 0,
    ) -> dict[str, Any]:
        """在课程中创建视频类型小节并绑定视频资源.

        视频小节与 SCORM 小节共用 sessionType=11，但具有视频特有的参数：
        - 允许拖动播放条、允许倍速播放
        - 最小学习时长、学习时长统计上限
        - 首次进入弹出视频说明
        - 视频说明支持纯文本或富文本

        流程：
        1. 上传封面图（如果提供了本地图片路径）
        2. 创建富文本视频说明（如果提供了 desc_richtext）
        3. 调用 savesession 创建视频小节
        4. 调用 keywords/save 保存标签
        5. 调用 bind-upd 绑定视频资源和封面图资源

        Args:
            group_id: 课程 ID
            session_title: 小节标题
            video_resource_id: 视频资源 ID（从"我的音视频"获取）
            cover_image_path: 封面图本地路径（jpg/png），上传后作为小节封面
            cover_resource_id: 已上传的封面图资源 ID（与 cover_image_path 二选一）
            desc_plain: 纯文本视频说明
            desc_richtext: 富文本视频说明（HTML），与 desc_plain 二选一
            is_required: 是否必修（默认 True）
            allow_drag_track: 是否允许学员拖动播放条（默认 True）
            allow_adjust_speed: 是否允许学员倍速播放（默认 True）
            min_duration_seconds: 最小学习时长（秒，0=不限制）
            max_duration_seconds: 学习时长统计上限（秒，0=不限制）
            desc_first_remind: 是否首次进入小节页弹出视频说明（默认 False）
            tags: 标签文本列表
            sort_order: 排序序号，0 表示自动追加

        Returns:
            包含 session_id 等信息的字典

        Raises:
            RuntimeError: 创建小节失败
            ValueError: 参数不合法
        """
        # 参数校验
        if not video_resource_id:
            raise ValueError("video_resource_id 不能为空")
        self._validate_video_section_params(
            desc_plain=desc_plain or None,
            desc_richtext=desc_richtext or None,
            cover_image_path=cover_image_path or None,
            cover_resource_id=cover_resource_id or None,
        )

        # 1. 处理封面图上传
        actual_cover_resource_id = self._upload_cover_image(
            cover_image_path=cover_image_path or None,
        )
        if not actual_cover_resource_id:
            actual_cover_resource_id = cover_resource_id

        # 2. 处理视频说明
        multimedia_type, multimedia_id, desc = self._process_video_description(
            desc_plain=desc_plain or None,
            desc_richtext=desc_richtext or None,
        )

        # 3. 构造 setup（视频特有参数）
        setup = self._build_video_setup(
            allow_drag_track=allow_drag_track,
            allow_adjust_speed=allow_adjust_speed,
            min_duration_seconds=min_duration_seconds,
            max_duration_seconds=max_duration_seconds,
            desc_first_remind=desc_first_remind,
        )
        setup.update({
            "is_allow_download": 0,
            "isAllowDownload": 0,
            "ai_session_summary_switch": "0",
            "content_type": "mp4",
            "enable_ai_subtitles": "0",
            "close_comment_switch": 0,
            "is_comment_time_visible": "1",
        })

        # 4. 构造 sessionInfo
        session_info: dict[str, Any] = {
            "autoCheck": 1,
            "creatTime": "",
            "creatTimeShow": "",
            "groupId": "",
            "onlineUserCount": "",
            "resultType": "",
            "sessionId": "",
            "sessionInUse": False,
            "sessionIndex": sort_order,
            "sessionStatus": "",
            "sessionTitle": session_title,
            "sessionType": "11",
            "teacherId": "",
            "desc": desc,
            "totalCount": "",
            "studentRegFlag": False,
            "totalUserCount": "",
            "multimedia_type": multimedia_type,
            "multimedia_id": multimedia_id,
            "setup": setup,
            "extend": {},
            "subtitleInfo": {},
            "point_ratio": "1",
            "is_require": 1 if is_required else 0,
            "tags": [{"tag": str(tag)} for tag in (tags or [])],
        }

        # 5. 构造 sectionArr
        extend: dict[str, Any] = {
            "resource_video_id": video_resource_id,
            "cover_index": 999,
        }
        if actual_cover_resource_id:
            extend["custom_cover_resource_id"] = actual_cover_resource_id

        question_info = self._build_question_info(extend)
        session_data = self._build_session_data(session_info, question_info)

        # 6. 调用 savesession
        resp = self.client.post(
            self.client.desktop_url("/api/session/savesession"),
            data={
                "group_id": group_id,
                "session_data": json.dumps(session_data, ensure_ascii=False),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error(
                "savesession 错误响应: %s",
                json.dumps(resp, ensure_ascii=False),
            )
            raise RuntimeError(
                f"保存视频小节失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        result_data = resp.get("data", {})
        session_id = str(result_data.get("session_id", ""))

        if not session_id:
            raise RuntimeError("保存视频小节成功但返回的 session_id 为空")

        logger.info(
            "视频小节创建成功: session_id=%s, group_id=%s, video_resource_id=%s",
            session_id,
            group_id,
            video_resource_id,
        )

        # 7. 保存标签
        self._save_keywords(session_id, tags=(tags or []))

        # 8. 调用 bind-upd 绑定资源
        try:
            self._bind_resources_to_session(
                session_id=session_id,
                video_resource_id=video_resource_id,
                cover_resource_id=actual_cover_resource_id,
            )
        except Exception as e:
            logger.error("资源绑定失败: %s", e)
            raise RuntimeError(f"视频小节已创建但资源绑定失败: {e}")

        return {
            "session_id": session_id,
            "group_id": group_id,
            "title": session_title,
            "video_resource_id": video_resource_id,
            "cover_resource_id": actual_cover_resource_id or None,
            "is_required": is_required,
            "allow_drag_track": allow_drag_track,
            "allow_adjust_speed": allow_adjust_speed,
            "min_duration_seconds": min_duration_seconds,
            "max_duration_seconds": max_duration_seconds,
            "desc_first_remind": desc_first_remind,
            "multimedia_type": multimedia_type,
            "multimedia_id": multimedia_id,
        }

    # ------------------------------------------------------------------
    # 创建文章小节
    # ------------------------------------------------------------------

    def create_article_section(
        self,
        group_id: str,
        session_title: str,
        article_content: str,
        cover_image_path: str = "",
        cover_resource_id: str = "",
        is_required: bool = True,
        type_name: str = "",
        min_duration_seconds: int = 0,
        max_duration_seconds: int = 0,
        show_course_creator_info: bool = True,
        show_article_reading_speed: bool = True,
        is_comment_time_visible: bool = True,
        enable_comment: bool = True,
        tags: list[str] | None = None,
        sort_order: int = 0,
    ) -> dict[str, Any]:
        """在课程中创建文章类型小节.

        文章小节使用 sessionType="13"，内容通过富文本(fulltext)存储：
        1. 调用 fulltextadd(ref_type="article") 创建富文本容器
        2. 调用 fulltextupdcontent 写入完整 HTML 内容
        3. 调用 savesession 创建小节，multimedia_type=1 关联富文本

        封面图通过 sectionArr.questionInfo.extend.resource_id 绑定
        （不需要额外的 bind-upd 调用）。

        Args:
            group_id: 课程 ID
            session_title: 小节标题
            article_content: 文章 HTML 内容（必需）
            cover_image_path: 封面图本地路径（jpg/png），可选
            cover_resource_id: 已上传的封面图资源 ID（与 cover_image_path 二选一）
            is_required: 是否必修（默认 True）
            type_name: 小节类型标签名称（如 "导学"、"案例分析"）
            min_duration_seconds: 最小学习时长（秒，0=不限制）
            max_duration_seconds: 学习时长统计上限（秒，0=不限制）
            show_course_creator_info: 是否展示课程创建者信息（默认 True）
            show_article_reading_speed: 是否展示文章字数和阅读速度（默认 True）
            is_comment_time_visible: 是否允许学员查看发言的提交时间（默认 True）
            enable_comment: 是否开启发言区（默认 True）
            tags: 标签文本列表
            sort_order: 排序序号，0 表示自动追加

        Returns:
            包含 session_id 等信息的字典

        Raises:
            RuntimeError: 创建小节失败
            ValueError: 参数不合法
        """
        if not article_content:
            raise ValueError("article_content 不能为空")

        if cover_image_path and cover_resource_id:
            raise ValueError(
                "cover_image_path 和 cover_resource_id 不能同时提供，请二选一"
            )

        # 1. 上传封面图（如有）
        actual_cover_resource_id = self._upload_cover_image(
            cover_image_path=cover_image_path or None,
        )
        if not actual_cover_resource_id:
            actual_cover_resource_id = cover_resource_id

        # 2. 创建文章内容（富文本）
        # 使用 _create_fulltext 直接传入内容（避免 fulltextupdcontent 超时）
        multimedia_id = self._create_fulltext(
            content=article_content,
            ref_type="article",
        )
        logger.info(
            "文章富文本创建成功: multimedia_id=%s, content_length=%d",
            multimedia_id,
            len(article_content),
        )

        # 3. 构造 setup（文章小节特有参数）
        setup: dict[str, Any] = {
            "allow_drag_track": "0",
            "allow_adjust_speed": 1,
            "is_allow_download": 0,
            "isAllowDownload": 0,
            "vlt_min": min_duration_seconds,
            "vlt_max": max_duration_seconds,
            "show_course_creator_info": "1" if show_course_creator_info else "0",
            "show_article_reading_speed": "1" if show_article_reading_speed else "0",
            "close_comment_switch": 0 if enable_comment else 1,
            "is_comment_time_visible": "1" if is_comment_time_visible else "0",
        }
        if type_name:
            setup["type_name"] = type_name

        # 4. 构造 sessionInfo
        session_info: dict[str, Any] = {
            "autoCheck": 1,
            "creatTime": "",
            "creatTimeShow": "",
            "groupId": "",
            "onlineUserCount": "",
            "resultType": "",
            "sessionId": "",
            "sessionInUse": False,
            "sessionIndex": sort_order,
            "sessionStatus": "",
            "sessionTitle": session_title,
            "sessionType": "13",
            "teacherId": "",
            "desc": "",
            "totalCount": "",
            "studentRegFlag": False,
            "totalUserCount": "",
            "multimedia_type": 1,
            "multimedia_id": multimedia_id,
            "setup": setup,
            "extend": {},
            "point_ratio": "1",
            "is_require": 1 if is_required else 0,
            "tags": [{"tag": str(tag)} for tag in (tags or [])],
        }

        # 5. 构造 sectionArr（封面图绑定）
        extend: dict[str, Any] = {}
        if actual_cover_resource_id:
            extend["resource_id"] = actual_cover_resource_id

        question_info = self._build_question_info(extend)
        session_data = self._build_session_data(session_info, question_info)

        # 6. 调用 savesession
        resp = self.client.post(
            self.client.desktop_url("/api/session/savesession"),
            data={
                "group_id": group_id,
                "session_data": json.dumps(session_data, ensure_ascii=False),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error(
                "savesession 错误响应: %s",
                json.dumps(resp, ensure_ascii=False),
            )
            raise RuntimeError(
                f"保存文章小节失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        result_data = resp.get("data", {})
        session_id = str(result_data.get("session_id", ""))

        if not session_id:
            raise RuntimeError("保存文章小节成功但返回的 session_id 为空")

        logger.info(
            "文章小节创建成功: session_id=%s, group_id=%s, multimedia_id=%s",
            session_id,
            group_id,
            multimedia_id,
        )

        # 7. 保存标签
        self._save_keywords(session_id, tags=(tags or []))

        return {
            "session_id": session_id,
            "group_id": group_id,
            "title": session_title,
            "multimedia_id": multimedia_id,
            "cover_resource_id": actual_cover_resource_id or None,
            "is_required": is_required,
            "type_name": type_name,
            "min_duration_seconds": min_duration_seconds,
            "max_duration_seconds": max_duration_seconds,
        }

    # ------------------------------------------------------------------
    # 修改视频小节
    # ------------------------------------------------------------------

    # 只读字段：修改时必须过滤，禁止回传
    _SESSION_READONLY_FIELDS: frozenset[str] = frozenset({
        "weikeStat",
        "liveStat",
        "totalStat",
        "onlineUserCount",
        "totalUserCount",
        "shareQrc",
        "resultUrl",
        "share_card_view",
        "result_card_view",
        "resultQrc",
        "miniProgramQrc",
        "sessionInUse",
        "creatTimeShow",
        "richText",
    })

    _SETUP_READONLY_FIELDS: frozenset[str] = frozenset({
        "serverTime",
        "create_device",
    })

    def update_video_section(
        self,
        group_id: str,
        session_id: str,
        session_title: str | None = None,
        video_resource_id: str | None = None,
        cover_image_path: str | None = None,
        cover_resource_id: str | None = None,
        remove_cover: bool = False,
        desc_plain: str | None = None,
        desc_richtext: str | None = None,
        is_required: bool | None = None,
        allow_drag_track: bool | None = None,
        allow_adjust_speed: bool | None = None,
        min_duration_seconds: int | None = None,
        max_duration_seconds: int | None = None,
        desc_first_remind: bool | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """修改已有视频小节的属性.

        基于 HAR 分析：修改视频小节使用与创建相同的 savesession API，
        区别在于必须传入 session_id 指定已有小节。

        流程：
        1. 调用 getsessionbaseinfo 获取现有数据
        2. 根据传入参数选择性修改字段
        3. 处理封面图变更（上传/替换/移除）
        4. 处理视频说明变更（纯文本/富文本）
        5. 过滤只读字段
        6. 调用 savesession 保存修改
        7. 如果需要更换视频资源，调用 bind-upd 解绑旧资源+绑定新资源
        8. 调用 keywords/save 保存标签（如果修改了标签）

        Args:
            group_id: 课程 ID
            session_id: 小节 ID
            session_title: 新标题（None=不修改）
            video_resource_id: 新视频资源 ID（None=不修改）
            cover_image_path: 新封面图本地路径（None=不修改）
            cover_resource_id: 已上传的封面图资源 ID（None=不修改）
            remove_cover: 是否移除封面图（默认 False）
            desc_plain: 新纯文本说明（None=不修改）
            desc_richtext: 新富文本说明 HTML（None=不修改）
            is_required: 是否必修（None=不修改）
            allow_drag_track: 是否允许拖动播放条（None=不修改）
            allow_adjust_speed: 是否允许倍速播放（None=不修改）
            min_duration_seconds: 最小学习时长秒数（None=不修改）
            max_duration_seconds: 学习时长统计上限秒数（None=不修改）
            desc_first_remind: 是否首次进入小节页弹出视频说明（None=不修改）
            tags: 新标签列表（None=不修改，[]=清空标签）

        Returns:
            包含更新后信息的字典

        Raises:
            RuntimeError: 获取现有数据失败或保存失败
            ValueError: 参数冲突
        """
        # 参数校验
        self._validate_video_section_params(
            desc_plain=desc_plain,
            desc_richtext=desc_richtext,
            cover_image_path=cover_image_path,
            cover_resource_id=cover_resource_id,
            remove_cover=remove_cover,
        )

        # 1. 获取现有小节数据
        logger.info("获取小节现有数据: session_id=%s", session_id)
        resp = self.client.get(
            self.client.desktop_url("/api/session/getsessionbaseinfo"),
            params={"session_id": session_id},
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(
                f"获取小节数据失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        raw_data = resp.get("data", {})
        if not raw_data:
            raise RuntimeError("getsessionbaseinfo 返回空数据")

        # 2. 提取现有 sessionInfo 数据
        session_info = dict(raw_data)

        # 确认小节类型是视频（sessionType=11, content_type=mp4）
        session_type = str(session_info.get("sessionType", ""))
        setup = dict(session_info.get("setup", {}) or {})
        content_type = str(setup.get("content_type", ""))
        if session_type != "11" or content_type != "mp4":
            raise RuntimeError(
                f"小节类型不匹配：sessionType={session_type}, content_type={content_type}，"
                f"期望视频小节（sessionType=11, content_type=mp4）"
            )

        # 3. 记录原始值（用于 bind-upd 判断是否需要更换资源）
        old_video_resource_id = ""
        old_cover_resource_id = ""
        extend = dict(session_info.get("extend", {}) or {})

        # 从 list_sections 获取完整资源绑定信息
        try:
            sections = self.list_sections(group_id, page=1, size=100)
            for s in sections:
                if str(s.get("session_id", "")) == str(session_id):
                    old_video_resource_id = s.get("resource_id", "")
                    old_cover_resource_id = s.get("cover_resource_id", "") or ""
                    break
        except Exception as e:
            logger.warning("获取小节资源绑定信息失败: %s", e)

        # 4. 根据传入参数修改字段
        modified_fields: list[str] = []

        if session_title is not None:
            session_info["sessionTitle"] = session_title
            modified_fields.append("session_title")

        if is_required is not None:
            session_info["is_require"] = 1 if is_required else 0
            modified_fields.append("is_require")

        # setup 参数（增量更新）
        setup_update = self._build_video_setup(
            allow_drag_track=allow_drag_track,
            allow_adjust_speed=allow_adjust_speed,
            min_duration_seconds=min_duration_seconds,
            max_duration_seconds=max_duration_seconds,
            desc_first_remind=desc_first_remind,
        )
        for key, value in setup_update.items():
            setup[key] = value
            modified_fields.append(key)

        # 5. 处理视频说明变更
        current_mm_type = session_info.get("multimedia_type", 0)
        current_mm_id = session_info.get("multimedia_id", 0)
        current_desc = session_info.get("desc", "")

        multimedia_type, multimedia_id, desc = self._process_video_description(
            desc_plain=desc_plain,
            desc_richtext=desc_richtext,
            current_multimedia_type=current_mm_type,
            current_multimedia_id=current_mm_id,
            current_desc=current_desc,
            fatal_richtext=True,
        )

        if desc_richtext is not None:
            modified_fields.append("desc_richtext")
        elif desc_plain is not None:
            modified_fields.append("desc_plain")

        session_info["multimedia_type"] = multimedia_type
        session_info["multimedia_id"] = multimedia_id
        session_info["desc"] = desc

        # 6. 处理封面图变更
        new_cover_resource_id = old_cover_resource_id

        if remove_cover:
            new_cover_resource_id = ""
            modified_fields.append("remove_cover")
            extend["cover_index"] = 0
            extend.pop("custom_cover_resource_id", None)
        elif cover_image_path is not None:
            new_cover_resource_id = self._upload_cover_image(
                cover_image_path=cover_image_path,
                fatal=True,
            )
            modified_fields.append("cover_image")
        elif cover_resource_id is not None:
            new_cover_resource_id = cover_resource_id
            modified_fields.append("cover_resource_id")

        if new_cover_resource_id:
            extend["custom_cover_resource_id"] = new_cover_resource_id
            extend["cover_index"] = 999

        # 7. 更新 setup 和 extend
        session_info["setup"] = setup
        session_info["extend"] = extend

        # 8. 处理标签变更
        if tags is not None:
            session_info["tags"] = [{"tag": str(tag)} for tag in tags]
            modified_fields.append("tags")

        # 9. 过滤只读字段
        self._filter_readonly_fields(session_info)

        # 10. 构造 session_data
        question_info = self._build_question_info(extend)
        session_data = self._build_session_data(session_info, question_info)

        # 11. 调用 savesession
        logger.info(
            "保存视频小节修改: session_id=%s, modified=%s",
            session_id,
            modified_fields,
        )
        resp = self.client.post(
            self.client.desktop_url("/api/session/savesession"),
            data={
                "group_id": group_id,
                "session_id": session_id,
                "session_data": json.dumps(session_data, ensure_ascii=False),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error(
                "savesession 错误响应: %s",
                json.dumps(resp, ensure_ascii=False),
            )
            raise RuntimeError(
                f"保存视频小节修改失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        logger.info("视频小节修改保存成功: session_id=%s", session_id)

        # 12. 资源绑定更新（统一使用 _bind_resources_to_session）
        try:
            self._bind_resources_to_session(
                session_id=session_id,
                video_resource_id=video_resource_id if video_resource_id is not None else "",
                cover_resource_id=new_cover_resource_id,
                old_video_resource_id=old_video_resource_id,
                old_cover_resource_id=old_cover_resource_id,
            )
            if video_resource_id is not None and video_resource_id != old_video_resource_id:
                modified_fields.append("video_resource_id")
        except Exception as e:
            logger.error("资源绑定更新失败: %s", e)
            raise RuntimeError(f"视频小节已修改但资源绑定更新失败: {e}")

        # 13. 保存标签
        self._save_keywords(session_id, tags=tags)

        return {
            "session_id": session_id,
            "group_id": group_id,
            "modified_fields": modified_fields,
            "title": session_title or session_info.get("sessionTitle", ""),
            "video_resource_id": video_resource_id or old_video_resource_id,
            "cover_resource_id": new_cover_resource_id or None,
            "is_required": is_required if is_required is not None else bool(
                session_info.get("is_require", 0)
            ),
        }

    # ------------------------------------------------------------------
    # 创建文档小节
    # ------------------------------------------------------------------

    def create_document_session(
        self,
        group_id: str,
        session_title: str,
        resource_id: str,
        desc_plain: str = "",
        desc_richtext: str = "",
        is_required: bool = True,
        allow_download: bool = True,
        min_duration_seconds: int = 0,
        finish_condition: str = "open",
        show_creator_info: bool = True,
        enable_comment: bool = True,
        show_comment_time: bool = True,
        tags: list[str] | None = None,
        cover_resource_id: str = "",
        sort_order: int = 0,
    ) -> dict[str, Any]:
        """在课程中创建文档类型小节并绑定资源.

        基于 HAR 分析：文档小节 sessionType="14"，资源绑定在
        sectionArr.extend.resource_id（注意不是 SCORM 的 resource_video_id）。
        文档小节不需要额外的 bind-upd 调用。

        文档说明支持两种方式（二选一）：
        - 富文本：提供 desc_richtext → 创建 multimedia，multimedia_type=1
        - 纯文本：提供 desc_plain → 写入 desc 字段，multimedia_type=0

        Args:
            group_id: 课程 ID
            session_title: 小节标题
            resource_id: 文档资源 ID（从"我的文档"获取）
            desc_plain: 纯文本文档说明
            desc_richtext: 富文本文档说明（HTML）
            is_required: 是否必修（默认 True）
            allow_download: 是否允许下载（默认 True）
            min_duration_seconds: 最小学习时长（秒，默认 0=不限制）
            finish_condition: 完成条件，"open"=打开即完成（默认），
                "last_page"=学完最后一页
            show_creator_info: 是否展示课程创建者信息（默认 True）
            enable_comment: 是否开启发言区（默认 True）
            show_comment_time: 是否允许查看发言提交时间（默认 True）
            tags: 标签文本列表
            cover_resource_id: 小节封面图资源 ID（可选）
            sort_order: 排序序号，0 表示自动追加

        Returns:
            包含 session_id 等信息的字典

        Raises:
            RuntimeError: 创建小节失败
            ValueError: 参数不合法
        """
        # 参数校验
        if not resource_id:
            raise ValueError("resource_id 不能为空")

        if finish_condition not in ("open", "last_page"):
            raise ValueError(
                f"finish_condition 必须是 'open' 或 'last_page'，"
                f"收到: {finish_condition}"
            )

        # 文档说明方式校验（二选一或不填）
        if desc_plain and desc_richtext:
            raise ValueError(
                "desc_plain 和 desc_richtext 不能同时提供，请二选一"
            )

        # 1. 处理文档说明
        multimedia_type = 0
        multimedia_id: str | int = 0
        desc = ""

        if desc_richtext:
            # 富文本模式：创建 multimedia 内容
            try:
                multimedia_id = self._create_fulltext(desc_richtext)
                multimedia_type = 1
                logger.info(
                    "文档说明富文本创建成功: multimedia_id=%s",
                    multimedia_id,
                )
            except Exception as e:
                logger.warning(
                    "文档说明富文本创建失败（非致命，回退到空）: %s",
                    e,
                )
                multimedia_id = 0
                multimedia_type = 0
        elif desc_plain:
            # 纯文本模式
            desc = desc_plain
            multimedia_type = 0
            multimedia_id = 0

        # 2. 构造 setup
        finish_map = {"open": "1", "last_page": "2"}
        setup: dict[str, Any] = {
            "allow_drag_track": "0",
            "allow_adjust_speed": 1,
            "is_allow_download": 0,
            "isAllowDownload": "1" if allow_download else "0",
            "vlt_min": min_duration_seconds,
            "vlt_max": 0,
            "document_finished_condition": finish_map[finish_condition],
            "show_course_creator_info": "1" if show_creator_info else "0",
            "close_comment_switch": 0 if enable_comment else 1,
            "is_comment_time_visible": "1" if show_comment_time else "0",
            "type_name": "课件",
        }

        # 3. 构造 sessionInfo
        session_info: dict[str, Any] = {
            "autoCheck": 1,
            "creatTime": "",
            "creatTimeShow": "",
            "groupId": "",
            "onlineUserCount": "",
            "resultType": "",
            "sessionId": "",
            "sessionInUse": False,
            "sessionIndex": sort_order,
            "sessionStatus": "",
            "sessionTitle": session_title,
            "sessionType": "14",
            "teacherId": "",
            "desc": desc,
            "totalCount": "",
            "studentRegFlag": False,
            "totalUserCount": "",
            "multimedia_type": multimedia_type,
            "multimedia_id": multimedia_id,
            "setup": setup,
            "extend": {},
            "point_ratio": "1",
            "tags": [{"tag": str(tag)} for tag in (tags or [])],
            "is_require": "1" if is_required else 0,
        }

        # 4. 构造 sectionArr
        extend: dict[str, Any] = {
            "resource_id": resource_id,
        }
        if cover_resource_id:
            extend["custom_cover_resource_id"] = cover_resource_id

        question_info: dict[str, Any] = {
            "questionId": "",
            "sessionId": "",
            "questionTitle": "",
            "questionIndex": "",
            "pattern": "",
            "required": "",
            "creatTime": "",
            "creatTimeShow": "",
            "domType": "weike",
            "totalCount": "",
            "showType": {},
            "showIndex": 0,
            "setup": {},
            "extend": extend,
        }

        session_data = {
            "sessionInfo": session_info,
            "sectionArr": [
                {
                    "questionInfo": question_info,
                    "answerArr": [],
                }
            ],
        }

        # 5. 调用 savesession
        resp = self.client.post(
            self.client.desktop_url("/api/session/savesession"),
            data={
                "group_id": group_id,
                "session_data": json.dumps(session_data, ensure_ascii=False),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error(
                "savesession 错误响应: %s",
                json.dumps(resp, ensure_ascii=False),
            )
            raise RuntimeError(
                f"保存文档小节失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        result_data = resp.get("data", {})
        session_id = str(result_data.get("session_id", ""))

        if not session_id:
            raise RuntimeError("保存文档小节成功但返回的 session_id 为空")

        logger.info(
            "文档小节创建成功: session_id=%s, group_id=%s, resource_id=%s",
            session_id,
            group_id,
            resource_id,
        )

        return {
            "session_id": session_id,
            "group_id": group_id,
            "title": session_title,
            "resource_id": resource_id,
            "cover_resource_id": cover_resource_id or None,
            "is_required": is_required,
            "allow_download": allow_download,
            "finish_condition": finish_condition,
            "multimedia_type": multimedia_type,
            "multimedia_id": multimedia_id,
        }

    @staticmethod
    def _extract_leaf_category_ids(category_arr: list[dict[str, Any]]) -> list[str]:
        """从嵌套的 categoryArr 中提取所有叶子节点 ID.

        categoryArr 返回的是嵌套结构，每个节点有 sub_category 子数组。
        叶子节点是没有子分类的节点，代表实际的分类选择。

        Args:
            category_arr: getgroupinfo 返回的 categoryArr

        Returns:
            叶子节点 ID 列表
        """
        leaf_ids: list[str] = []
        for cat in category_arr:
            sub_cats = cat.get("sub_category", [])
            if sub_cats:
                leaf_ids.extend(CourseBuilder._extract_leaf_category_ids(sub_cats))
            else:
                leaf_ids.append(str(cat.get("id", "")))
        return leaf_ids

    # ------------------------------------------------------------------
    # 提交课程审核至企业知识库
    # ------------------------------------------------------------------

    def submit_course_for_audit(self, group_id: str) -> dict[str, Any]:
        """将课程提交至企业知识库进行审核.

        对应 HAR 中的 POST /api/group/submitcourse 调用。
        提交后课程进入管理员审核流程，审核通过后会被推荐并支持搜索。

        Args:
            group_id: 课程 ID

        Returns:
            API 原始响应字典（包含 release_status / audit_status 等）

        Raises:
            RuntimeError: 提交失败或网络错误
        """
        self._write_cooldown()

        resp = self.client.post(
            self.client.desktop_url("/api/group/submitcourse"),
            data={"group_id": str(group_id)},
        )

        self._mark_write()

        # UMU 此接口在成功时 status=true/error_code=0，但顶层 success 字段可能为 false
        if resp.get("status") is True or resp.get("error_code") == 0:
            logger.info("提交课程审核成功: group_id=%s", group_id)
            return resp

        error = resp.get("error") or resp.get("error_message") or "提交课程审核失败"
        logger.error("提交课程审核失败: group_id=%s, resp=%s", group_id, resp)
        raise RuntimeError(error)

    # ------------------------------------------------------------------
    # 获取课程分类树
    # ------------------------------------------------------------------

    def get_category_tree(self, use_cache: bool = True) -> list[dict[str, Any]]:
        """从课程首页 HTML 中提取当前账号的课程分类树.

        分类数据嵌入在 /course/index 页面的 window.pageData.data.platform.category
        中，通过服务器端渲染注入。由于所有 /ajax/category/* API 返回权限错误，
        页面解析是唯一可靠的获取方式。

        结果按 username 缓存 5 分钟，避免重复请求。

        Args:
            use_cache: 是否使用缓存（默认 True）

        Returns:
            分类树列表，每个节点包含 id, name, parent_id, sub_category

        Raises:
            RuntimeError: 获取失败
        """
        # 获取缓存 key（使用 client 的 username）
        username = getattr(self.client, "username", "") or "default"
        now = datetime.now()

        if use_cache and username in self._category_cache:
            tree, cached_at = self._category_cache[username]
            if now - cached_at < self._CACHE_TTL:
                logger.info("使用缓存的分类树 (username=%s)", username)
                return tree

        # 重新获取
        resp = self.client.http.get(
            f"{self.client.base_url}/course/index",
        )
        html = resp.text

        # 查找 window.pageData = {...}
        start = html.find("window.pageData = ")
        if start < 0:
            raise RuntimeError("无法在页面中找到 window.pageData")

        json_start = start + len("window.pageData = ")

        # 使用计数器精确匹配 JSON 边界（处理嵌套和字符串）
        brace_count = 0
        in_string = False
        escape_next = False
        end_idx = json_start

        for i in range(json_start, len(html)):
            char = html[i]
            if escape_next:
                escape_next = False
                continue
            if char == "\\":
                escape_next = True
                continue
            if char == '"' and not escape_next:
                in_string = not in_string
                continue
            if not in_string:
                if char == "{":
                    brace_count += 1
                elif char == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i + 1
                        break

        try:
            page_data = json.loads(html[json_start:end_idx])
        except json.JSONDecodeError as e:
            raise RuntimeError(f"解析 pageData 失败: {e}")

        categories = page_data.get("data", {}).get("platform", {}).get("category", [])
        logger.info("获取分类树成功: %d 个顶级分类", len(categories))

        # 写入缓存
        self._category_cache[username] = (categories, now)
        return categories

    # ------------------------------------------------------------------
    # 写操作冷却期（避免连续调用触发 503）
    # ------------------------------------------------------------------

    @classmethod
    def _write_cooldown(cls) -> float:
        """检查并在需要时等待写操作冷却期.

        返回实际等待的秒数（0 表示无需等待）。
        """
        now = time.time()
        elapsed = now - cls._last_write_time
        if elapsed < cls._WRITE_COOLDOWN:
            wait = cls._WRITE_COOLDOWN - elapsed
            logger.debug("写操作冷却期: 等待 %.1f 秒", wait)
            time.sleep(wait)
            return wait
        return 0.0

    @classmethod
    def _mark_write(cls) -> None:
        """记录本次写操作时间戳."""
        cls._last_write_time = time.time()

    # ------------------------------------------------------------------
    # API 调用重试（限流防护）
    # ------------------------------------------------------------------

    def _post_with_retry(
        self,
        endpoint: str,
        data: dict[str, Any],
        max_retries: int = 3,
        retry_delay: float = 10.0,
        backoff_factor: float = 2.0,
        max_delay: float = 60.0,
        jitter: bool = True,
    ) -> dict[str, Any]:
        """带重试的 POST 请求，用于 e_saveGroup 等写操作.

        当服务端返回 503/429/网络错误时自动重试，使用指数退避 + jitter 策略。
        4xx 客户端错误不重试。

        Args:
            endpoint: API 端点路径（如 "/ajax/e_saveGroup"）
            data: POST 请求数据
            max_retries: 最大重试次数（默认 3）
            retry_delay: 初始重试间隔秒数（默认 10）
            backoff_factor: 退避倍数（默认 2）
            max_delay: 最大重试间隔秒数（默认 60）
            jitter: 是否启用随机偏移（默认 True，偏移量 0~30%）

        Returns:
            API 响应字典

        Raises:
            RuntimeError: 重试耗尽后仍失败
        """
        import random

        last_error: Exception | None = None
        delay = retry_delay

        for attempt in range(max_retries + 1):
            try:
                resp = self.client.post(
                    self.client.desktop_url(endpoint),
                    data=data,
                )

                # 检查是否成功
                if resp.get("status") is True or resp.get("error_code") == 0:
                    return resp

                # 检查是否需要重试的错误
                err_msg = str(resp.get("errMsg") or resp.get("error", ""))
                lower_msg = err_msg.lower()

                is_retryable = any(
                    keyword in lower_msg
                    for keyword in [
                        "503",
                        "service unavailable",
                        "429",
                        "too many requests",
                        "rate limit",
                        "temporarily",
                    ]
                )

                if not is_retryable or attempt == max_retries:
                    # 非重试错误或最后一次尝试 — 直接返回
                    return resp

                actual_delay = delay * (1 + random.uniform(0, 0.3)) if jitter else delay
                logger.warning(
                    "API 限流 (%s)，第 %d/%d 次重试，%.1f 秒后重试: %s",
                    endpoint,
                    attempt + 1,
                    max_retries,
                    actual_delay,
                    err_msg,
                )

            except Exception as e:
                last_error = e
                err_str = str(e).lower()

                # 检查是否为可重试的服务器错误（503/429/502/504）或网络错误
                is_retryable_error = any(
                    keyword in err_str
                    for keyword in [
                        # HTTP 状态码
                        "503",
                        "429",
                        "502",
                        "504",
                        "service unavailable",
                        "too many requests",
                        "bad gateway",
                        "gateway timeout",
                        # 网络相关
                        "connection",
                        "timeout",
                        "network",
                        "refused",
                        "reset",
                    ]
                )

                # 也检查异常对象的 status 属性（如果有）
                if not is_retryable_error and hasattr(e, "status"):
                    status_code = getattr(e, "status", 0)
                    if status_code in (502, 503, 504, 429):
                        is_retryable_error = True

                if not is_retryable_error or attempt == max_retries:
                    raise RuntimeError(
                        f"API 请求失败 ({endpoint}): {e}"
                    ) from e

                actual_delay = delay * (1 + random.uniform(0, 0.3)) if jitter else delay
                logger.warning(
                    "可重试错误 (%s)，第 %d/%d 次重试，%.1f 秒后重试: %s",
                    endpoint,
                    attempt + 1,
                    max_retries,
                    actual_delay,
                    e,
                )

            # 等待后重试
            if attempt < max_retries:
                time.sleep(actual_delay)
                delay = min(delay * backoff_factor, max_delay)

        # 不应该到达这里，但为了类型安全
        if last_error:
            raise RuntimeError(f"API 请求失败 ({endpoint}): {last_error}")
        raise RuntimeError(f"API 请求失败 ({endpoint})，重试已耗尽")

    def clear_category_cache(self) -> None:
        """清除分类缓存（用于强制刷新）."""
        username = getattr(self.client, "username", "") or "default"
        if username in self._category_cache:
            del self._category_cache[username]
            logger.info("已清除分类缓存 (username=%s)", username)
        else:
            self._category_cache.clear()
            logger.info("已清除所有分类缓存")

    def resolve_category_names(
        self, names: list[str]
    ) -> list[tuple[str, str, list[str]]]:
        """将分类名称列表解析为 (id, name, path_chain) 元组列表.

        支持通过完整路径（如"课程系列 > 新能力系列 > 客户思维"）或仅叶子节点
        名称来定位分类。如果名称不唯一，优先返回第一个匹配。

        Args:
            names: 分类名称或路径列表

        Returns:
            每个元素为 (category_id, matched_name, path_chain) 的列表
            path_chain 是从根到该节点的完整路径名称列表

        Raises:
            RuntimeError: 某个名称找不到匹配
        """
        tree = self.get_category_tree()

        # 构建名称索引：name -> list of (id, path_chain)
        # 同时构建路径索引：path_string -> (id, path_chain)
        name_index: dict[str, list[tuple[str, list[str]]]] = {}
        path_index: dict[str, tuple[str, list[str]]] = {}

        def walk(node: dict[str, Any], path: list[str]) -> None:
            node_id = str(node.get("id", ""))
            node_name = str(node.get("name", ""))
            current_path = path + [node_name]

            # 添加到名称索引
            if node_name not in name_index:
                name_index[node_name] = []
            name_index[node_name].append((node_id, current_path))

            # 添加到路径索引（多种形式）
            path_str = " > ".join(current_path)
            path_index[path_str] = (node_id, current_path)
            # 也添加从父路径开始的形式（跳过根）
            if len(current_path) > 1:
                path_index[" > ".join(current_path[1:])] = (node_id, current_path)

            for sub in node.get("sub_category", []):
                walk(sub, current_path)

        for root in tree:
            walk(root, [])

        results: list[tuple[str, str, list[str]]] = []
        unmatched: list[str] = []

        for name in names:
            name = name.strip()
            if not name:
                continue

            # 先尝试路径匹配
            if name in path_index:
                cat_id, path_chain = path_index[name]
                results.append((cat_id, name, path_chain))
                logger.info("分类路径匹配成功: '%s' -> id=%s", name, cat_id)
                continue

            # 再尝试名称匹配
            if name in name_index:
                matches = name_index[name]
                if len(matches) == 1:
                    cat_id, path_chain = matches[0]
                    results.append((cat_id, name, path_chain))
                    logger.info("分类名称匹配成功: '%s' -> id=%s, path=%s", name, cat_id, " > ".join(path_chain))
                else:
                    # 多个匹配，记录所有选项让用户选择
                    options = [f"{' > '.join(p)} (id: {cid})" for cid, p in matches]
                    logger.warning("分类名称 '%s' 有 %d 个匹配: %s", name, len(matches), "; ".join(options))
                    # 选择第一个作为默认，但包含警告信息
                    cat_id, path_chain = matches[0]
                    results.append((cat_id, name, path_chain))
                continue

            unmatched.append(name)

        if unmatched:
            available = sorted(name_index.keys())
            raise RuntimeError(
                f"找不到分类: {unmatched}. "
                f"可用分类名称（共 {len(available)} 个）: {', '.join(available[:30])}"
                f"{'...' if len(available) > 30 else ''}"
            )

        return results

    # ------------------------------------------------------------------
    # 获取现有小节
    # ------------------------------------------------------------------

    def list_sections(
        self,
        group_id: str,
        page: int = 1,
        size: int = 20,
    ) -> list[dict[str, Any]]:
        """列出课程中的所有小节.

        调用 getsessionlistbygroup 获取小节列表，返回结构化的摘要信息。

        Args:
            group_id: 课程 ID
            page: 页码（从1开始）
            size: 每页数量

        Returns:
            小节列表，每个元素包含：
            - session_id: 小节ID
            - title: 小节标题
            - type: 小节类型（如 weike）
            - is_required: 是否必修
            - resource_id: SCORM 资源ID
            - cover_resource_id: 封面资源ID
            - status: 状态
            - chapter_id: 章节ID

        Raises:
            RuntimeError: 获取失败
        """
        resp = self.client.get(
            self.client.desktop_url("/ajax/session/getsessionlistbygroup"),
            params={
                "group_id": group_id,
                "isFirstLoad": "true",
                "is_contain_chapter": 1,
                "page": page,
                "size": size,
                "status_str": "0,1",
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(
                f"获取小节列表失败: {resp.get('errMsg', resp.get('error', 'unknown'))}"
            )

        raw_list = resp.get("data", {}).get("list", [])
        result: list[dict[str, Any]] = []

        for item in raw_list:
            sinfo = item.get("sessionInfo", {})
            section_arr = item.get("sectionArr", [])
            resource_info = item.get("resource_info", {})

            # 资源 ID：SCORM 用 resource_video_id，文档用 resource_id
            resource_id = ""
            resource_type = ""
            cover_resource_id = ""
            if section_arr:
                extend = section_arr[0].get("questionInfo", {}).get("extend", {})
                if not isinstance(extend, dict):
                    extend = {}
                video_id = extend.get("resource_video_id", "")
                doc_id = extend.get("resource_id", "")
                if video_id:
                    resource_id = video_id
                    resource_type = "video"
                elif doc_id:
                    resource_id = doc_id
                    resource_type = "document"
                cover_resource_id = extend.get("custom_cover_resource_id", "")

            # 检测资源删除状态
            is_resource_deleted = False
            resource_status: dict[str, Any] = {}
            if resource_info:
                resource_status = {
                    "id": resource_info.get("id", ""),
                    "status": resource_info.get("status", ""),
                    "is_recycle": resource_info.get("is_recycle", ""),
                    "is_deleted": resource_info.get("is_deleted", ""),
                }
                is_resource_deleted = resource_info.get("is_recycle") == "1"

            # 小节类型：sessionType 映射为可读名称
            session_type = str(sinfo.get("sessionType", ""))
            type_map = {
                "11": "scorm",
                "14": "document",
                "15": "infographic",
            }

            result.append({
                "session_id": str(sinfo.get("sessionId", "")),
                "title": sinfo.get("sessionTitle", ""),
                "type": type_map.get(session_type, session_type),
                "session_type": session_type,
                "is_required": bool(sinfo.get("is_require", 0)),
                "resource_id": resource_id,
                "resource_type": resource_type,
                "cover_resource_id": cover_resource_id or None,
                "resource_status": resource_status,
                "is_resource_deleted": is_resource_deleted,
                "status": sinfo.get("status", ""),
                "chapter_id": sinfo.get("chapter_id", ""),
                "result_type": sinfo.get("resultType", ""),
            })

        return result

    def get_section(
        self,
        session_id: str,
    ) -> dict[str, Any]:
        """获取单个小节的完整详情.

        调用 getsessionInfo 获取最新、最完整的数据。
        返回的数据已过滤掉只读统计字段。

        Args:
            session_id: 小节 ID

        Returns:
            包含 sessionInfo（已过滤）和 sectionArr 的字典

        Raises:
            RuntimeError: 获取失败
        """
        detail = self._get_session_detail(session_id)

        # 过滤只读字段
        session_info = detail.get("sessionInfo", {})
        filtered_info = copy.deepcopy(session_info)
        for ro_field in _READONLY_SESSIONINFO_FIELDS:
            filtered_info.pop(ro_field, None)

        return {
            "session_id": session_id,
            "sessionInfo": filtered_info,
            "sectionArr": detail.get("sectionArr", []),
            "questionArr": detail.get("questionArr", []),
        }

    def _get_session_from_course(
        self,
        group_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        """从课程详情中获取指定小节的完整数据.

        调用 getsessionlistbygroup 获取小节列表，匹配目标小节。
        返回原始 session 字典（含 sessionInfo + sectionArr）。

        Args:
            group_id: 课程 ID
            session_id: 小节 ID

        Returns:
            完整的 session 字典

        Raises:
            RuntimeError: 课程不存在或小节不在课程中
        """
        resp = self.client.get(
            self.client.desktop_url("/ajax/session/getsessionlistbygroup"),
            params={
                "group_id": group_id,
                "isFirstLoad": "true",
                "is_contain_chapter": 1,
                "page": 1,
                "size": 20,
                "status_str": "0,1",
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(
                f"获取小节列表失败: {resp.get('errMsg', resp.get('error', 'unknown'))}"
            )

        session_list = resp.get("data", {}).get("list", [])

        for sess in session_list:
            session_info = sess.get("sessionInfo", {})
            if str(session_info.get("sessionId", "")) == str(session_id):
                return sess

        raise RuntimeError(
            f"小节不存在于课程中: session_id={session_id}, group_id={group_id}"
        )

    # ------------------------------------------------------------------
    # 获取小节详情（用于编辑前获取最新完整数据）
    # ------------------------------------------------------------------

    def _get_session_detail(
        self,
        session_id: str,
    ) -> dict[str, Any]:
        """调用 getsessionInfo 获取单条小节的完整、最新数据.

        比 getsessionlistbygroup 更实时，字段更完整（含 subtitleInfo 等）。

        Args:
            session_id: 小节 ID

        Returns:
            包含 sessionInfo / sectionArr / questionArr 的字典

        Raises:
            RuntimeError: 获取失败
        """
        resp = self.client.get(
            self.client.desktop_url("/ajax/session/getsessionInfo"),
            params={"session_id": session_id, "signindata": "0"},
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(
                f"获取小节详情失败: {resp.get('errMsg', resp.get('error', 'unknown'))}"
            )

        info = resp.get("data", {}).get("info", {})
        if not info:
            raise RuntimeError(f"获取小节详情返回空数据: session_id={session_id}")

        return info

    # ------------------------------------------------------------------
    # 更新 SCORM 小节
    # ------------------------------------------------------------------

    def update_scorm_session(
        self,
        group_id: str,
        session_id: str,
        session_title: str | None = None,
        resource_id: str | None = None,
        cover_resource_id: str | None = None,
        is_required: bool | None = None,
        duration_minutes: int | None = None,
    ) -> dict[str, Any]:
        """更新课程中已有的 SCORM 类型小节.

        采用"先获取完整数据，再应用变更"的模式：
        1. 调用 getsessionInfo 获取现有小节最新完整数据
        2. 过滤只读字段（统计数据等）
        3. 应用用户传入的变更（只改提供的字段）
        4. 调用 savesession 提交完整数据（必须包含 session_id）
        5. 如资源变更，调用 bind-upd 做 bind/unbind

        Args:
            group_id: 课程 ID
            session_id: 小节 ID
            session_title: 新小节标题（可选）
            resource_id: 新 SCORM 资源 ID（可选）
            cover_resource_id: 新封面图资源 ID（可选）
            is_required: 是否必修（可选）
            duration_minutes: 预计学习时长（分钟）（可选，当前未实现）

        Returns:
            包含 session_id 和 changes 列表的字典

        Raises:
            RuntimeError: 更新失败
        """
        # 1. 获取现有小节最新完整数据（使用 getsessionInfo 而非列表 API）
        existing = self._get_session_detail(session_id)
        session_info = existing.get("sessionInfo", {})

        # 2. 提取旧资源 ID（用于后续 unbind）
        old_resource_id = ""
        old_cover_resource_id = ""
        section_arr = existing.get("sectionArr", [])
        if section_arr:
            question_info = section_arr[0].get("questionInfo", {})
            old_extend = question_info.get("extend", {})
            old_resource_id = old_extend.get("resource_video_id", "")
            old_cover_resource_id = old_extend.get("custom_cover_resource_id", "")

        # 3. 深拷贝并过滤只读字段（防止覆盖统计数据和动态内容）
        updated_info = copy.deepcopy(session_info)
        for ro_field in _READONLY_SESSIONINFO_FIELDS:
            updated_info.pop(ro_field, None)

        # 4. 应用用户传入的变更
        changes: dict[str, Any] = {}

        if session_title is not None:
            updated_info["sessionTitle"] = session_title
            changes["session_title"] = session_title

        if is_required is not None:
            updated_info["is_require"] = 1 if is_required else 0
            changes["is_required"] = is_required

        # 4. 确定实际使用的 resource_id 和 cover_resource_id
        actual_resource_id = resource_id if resource_id is not None else old_resource_id
        actual_cover_id = (
            cover_resource_id
            if cover_resource_id is not None
            else old_cover_resource_id
        )

        if resource_id is not None and resource_id != old_resource_id:
            changes["resource_id"] = resource_id

        if cover_resource_id is not None and cover_resource_id != old_cover_resource_id:
            changes["cover_resource_id"] = cover_resource_id

        # 5. 构造 sectionArr
        extend: dict[str, Any] = {
            "resource_video_id": actual_resource_id,
            "cover_index": 999,
        }
        if actual_cover_id:
            extend["custom_cover_resource_id"] = actual_cover_id

        session_data = {
            "sessionInfo": updated_info,
            "sectionArr": [
                {
                    "questionInfo": {
                        "cid": 0,
                        "questionMode": "weike",
                        "questionId": 0,
                        "extend": extend,
                        "showIndex": 0,
                    },
                }
            ],
        }

        # 6. 调用 savesession 更新（关键：传入 session_id）
        resp = self.client.post(
            self.client.desktop_url("/api/session/savesession"),
            data={
                "group_id": group_id,
                "session_id": session_id,
                "session_data": json.dumps(session_data, ensure_ascii=False),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error(
                "savesession 更新错误: %s",
                json.dumps(resp, ensure_ascii=False),
            )
            raise RuntimeError(
                f"更新小节失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        logger.info(
            "小节更新成功: session_id=%s, changes=%s",
            session_id,
            list(changes.keys()),
        )

        # 7. 如资源变更，调用 bind-upd 更新绑定关系
        if (
            resource_id
            and resource_id != old_resource_id
            and old_resource_id
        ):
            try:
                self._update_resource_binding(
                    session_id=session_id,
                    new_resource_id=resource_id,
                    old_resource_id=old_resource_id,
                    cover_resource_id=actual_cover_id,
                )
            except Exception as e:
                logger.error("资源绑定更新失败: %s", e)
                raise RuntimeError(f"小节已更新但资源绑定关系更新失败: {e}")

        return {
            "session_id": session_id,
            "group_id": group_id,
            "changes": list(changes.keys()),
            "title": updated_info.get("sessionTitle", ""),
            "resource_id": actual_resource_id,
        }

    # ------------------------------------------------------------------
    # 更新文档小节
    # ------------------------------------------------------------------

    def update_document_session(
        self,
        group_id: str,
        session_id: str,
        session_title: str | None = None,
        resource_id: str | None = None,
        cover_resource_id: str | None = None,
        desc_plain: str | None = None,
        desc_richtext: str | None = None,
        is_required: bool | None = None,
        allow_download: bool | None = None,
        min_duration_seconds: int | None = None,
        finish_condition: str | None = None,
        show_creator_info: bool | None = None,
        enable_comment: bool | None = None,
        show_comment_time: bool | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """更新课程中已有的文档类型小节.

        采用"先获取完整数据，再应用变更"的模式：
        1. 调用 getsessionInfo 获取现有小节最新完整数据
        2. 过滤只读字段（统计数据等）
        3. 应用用户传入的变更（只改提供的字段）
        4. 调用 savesession 提交完整数据（必须包含 session_id）

        Args:
            group_id: 课程 ID
            session_id: 小节 ID
            session_title: 新小节标题（可选）
            resource_id: 新文档资源 ID（可选）
            cover_resource_id: 新封面图资源 ID（可选）
            desc_plain: 新纯文本文档说明（可选，与 desc_richtext 二选一）
            desc_richtext: 新富文本文档说明（可选，与 desc_plain 二选一）
            is_required: 是否必修（可选）
            allow_download: 是否允许下载（可选）
            min_duration_seconds: 最小学习时长（秒，可选）
            finish_condition: 完成条件（"open"/"last_page"，可选）
            show_creator_info: 是否展示创建者信息（可选）
            enable_comment: 是否开启发言区（可选）
            show_comment_time: 是否显示发言时间（可选）
            tags: 标签列表（可选）

        Returns:
            包含 session_id 和 changes 列表的字典

        Raises:
            RuntimeError: 更新失败
            ValueError: 参数不合法
        """
        # 参数校验
        if finish_condition is not None and finish_condition not in ("open", "last_page"):
            raise ValueError(
                f"finish_condition 必须是 'open' 或 'last_page'，"
                f"收到: {finish_condition}"
            )

        if desc_plain and desc_richtext:
            raise ValueError(
                "desc_plain 和 desc_richtext 不能同时提供，请二选一"
            )

        # 1. 获取现有小节最新完整数据
        existing = self._get_session_detail(session_id)
        session_info = existing.get("sessionInfo", {})

        # 2. 提取旧资源 ID 和封面 ID
        old_resource_id = ""
        old_cover_resource_id = ""
        section_arr = existing.get("sectionArr", [])
        if section_arr:
            question_info = section_arr[0].get("questionInfo", {})
            old_extend = question_info.get("extend", {})
            old_resource_id = old_extend.get("resource_id", "")
            old_cover_resource_id = old_extend.get("custom_cover_resource_id", "")

        # 3. 深拷贝并过滤只读字段
        updated_info = copy.deepcopy(session_info)
        for ro_field in _READONLY_SESSIONINFO_FIELDS:
            updated_info.pop(ro_field, None)

        # 4. 应用用户传入的变更
        changes: dict[str, Any] = {}

        if session_title is not None:
            updated_info["sessionTitle"] = session_title
            changes["session_title"] = session_title

        if is_required is not None:
            updated_info["is_require"] = "1" if is_required else 0
            changes["is_required"] = is_required

        # 处理文档说明变更
        if desc_plain is not None:
            updated_info["desc"] = desc_plain
            updated_info["multimedia_type"] = 0
            updated_info["multimedia_id"] = 0
            changes["desc"] = desc_plain
            changes["multimedia_type"] = 0

        if desc_richtext is not None:
            # 富文本模式：创建新的 multimedia
            try:
                multimedia_id = self._create_fulltext(desc_richtext)
                updated_info["multimedia_type"] = 1
                updated_info["multimedia_id"] = multimedia_id
                updated_info["desc"] = ""
                changes["multimedia_type"] = 1
                changes["multimedia_id"] = multimedia_id
            except Exception as e:
                logger.warning("富文本更新失败，保持原设置: %s", e)

        # 处理 setup 变更
        setup = updated_info.get("setup", {})
        if not isinstance(setup, dict):
            setup = {}
            updated_info["setup"] = setup

        if allow_download is not None:
            setup["isAllowDownload"] = "1" if allow_download else "0"
            changes["allow_download"] = allow_download

        if min_duration_seconds is not None:
            setup["vlt_min"] = min_duration_seconds
            changes["min_duration_seconds"] = min_duration_seconds

        if finish_condition is not None:
            finish_map = {"open": "1", "last_page": "2"}
            setup["document_finished_condition"] = finish_map[finish_condition]
            changes["finish_condition"] = finish_condition

        if show_creator_info is not None:
            setup["show_course_creator_info"] = "1" if show_creator_info else "0"
            changes["show_creator_info"] = show_creator_info

        if enable_comment is not None:
            setup["close_comment_switch"] = 0 if enable_comment else 1
            changes["enable_comment"] = enable_comment

        if show_comment_time is not None:
            setup["is_comment_time_visible"] = "1" if show_comment_time else "0"
            changes["show_comment_time"] = show_comment_time

        updated_info["setup"] = setup

        if tags is not None:
            updated_info["tags"] = [{"tag": str(tag)} for tag in tags]
            changes["tags"] = tags

        # 5. 确定实际使用的 resource_id 和 cover_resource_id
        actual_resource_id = resource_id if resource_id is not None else old_resource_id
        actual_cover_id = (
            cover_resource_id
            if cover_resource_id is not None
            else old_cover_resource_id
        )

        if resource_id is not None and resource_id != old_resource_id:
            changes["resource_id"] = resource_id

        if cover_resource_id is not None and cover_resource_id != old_cover_resource_id:
            changes["cover_resource_id"] = cover_resource_id

        # 6. 构造 sectionArr
        extend: dict[str, Any] = {
            "resource_id": actual_resource_id,
        }
        if actual_cover_id:
            extend["custom_cover_resource_id"] = actual_cover_id

        session_data = {
            "sessionInfo": updated_info,
            "sectionArr": [
                {
                    "questionInfo": {
                        "questionId": "",
                        "sessionId": "",
                        "questionTitle": "",
                        "questionIndex": "",
                        "pattern": "",
                        "required": "",
                        "creatTime": "",
                        "creatTimeShow": "",
                        "domType": "weike",
                        "totalCount": "",
                        "showType": {},
                        "showIndex": 0,
                        "setup": {},
                        "extend": extend,
                    },
                    "answerArr": [],
                }
            ],
        }

        # 7. 调用 savesession 更新
        resp = self.client.post(
            self.client.desktop_url("/api/session/savesession"),
            data={
                "group_id": group_id,
                "session_id": session_id,
                "session_data": json.dumps(session_data, ensure_ascii=False),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error(
                "savesession 更新错误: %s",
                json.dumps(resp, ensure_ascii=False),
            )
            raise RuntimeError(
                f"更新文档小节失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        logger.info(
            "文档小节更新成功: session_id=%s, changes=%s",
            session_id,
            list(changes.keys()),
        )

        return {
            "session_id": session_id,
            "group_id": group_id,
            "changes": list(changes.keys()),
            "title": updated_info.get("sessionTitle", ""),
            "resource_id": actual_resource_id,
        }

    # ------------------------------------------------------------------
    # 修改文章小节
    # ------------------------------------------------------------------

    def update_article_section(
        self,
        group_id: str,
        session_id: str,
        session_title: str | None = None,
        article_content: str | None = None,
        cover_image_path: str | None = None,
        cover_resource_id: str | None = None,
        remove_cover: bool = False,
        is_required: bool | None = None,
        type_name: str | None = None,
        min_duration_seconds: int | None = None,
        max_duration_seconds: int | None = None,
        show_course_creator_info: bool | None = None,
        show_article_reading_speed: bool | None = None,
        is_comment_time_visible: bool | None = None,
        enable_comment: bool | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """修改已有文章小节的属性.

        基于 HAR 分析：修改文章小节使用 savesession API（带 session_id），
        前置读取 getsessionbaseinfo 获取现有数据。

        流程：
        1. 调用 getsessionbaseinfo 获取现有小节数据
        2. 过滤只读字段
        3. 应用用户传入的变更（只改提供的字段）
        4. 如有文章内容变更 → fulltextupdcontent(ref_type="article")
        5. 如有封面变更 → 上传新封面
        6. 调用 savesession 保存修改
        7. 调用 keywords/save 保存标签

        Args:
            group_id: 课程 ID
            session_id: 小节 ID
            session_title: 新标题（None=不修改）
            article_content: 新文章 HTML 内容（None=不修改）
            cover_image_path: 新封面图本地路径（None=不修改）
            cover_resource_id: 已上传的封面图资源 ID（None=不修改）
            remove_cover: 是否移除封面图（默认 False）
            is_required: 是否必修（None=不修改）
            type_name: 小节类型标签（None=不修改）
            min_duration_seconds: 最小学习时长秒数（None=不修改）
            max_duration_seconds: 学习时长统计上限秒数（None=不修改）
            show_course_creator_info: 展示课程创建者信息（None=不修改）
            show_article_reading_speed: 展示文章字数和阅读速度（None=不修改）
            is_comment_time_visible: 允许查看发言提交时间（None=不修改）
            enable_comment: 开启发言区（None=不修改）
            tags: 新标签列表（None=不修改，[]=清空标签）

        Returns:
            包含修改字段列表和更新后信息的字典

        Raises:
            RuntimeError: 获取或保存失败
            ValueError: 参数冲突
        """
        # 参数校验
        if cover_image_path and cover_resource_id:
            raise ValueError(
                "cover_image_path 和 cover_resource_id 不能同时提供，请二选一"
            )
        if remove_cover and (cover_image_path or cover_resource_id):
            raise ValueError(
                "remove_cover 与 cover_image_path/cover_resource_id 不能同时使用"
            )

        # 1. 获取现有小节数据
        logger.info("获取文章小节现有数据: session_id=%s", session_id)
        resp = self.client.get(
            self.client.desktop_url("/api/session/getsessionbaseinfo"),
            params={"session_id": session_id},
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(
                f"获取小节数据失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        raw_data = resp.get("data", {})
        if not raw_data:
            raise RuntimeError("getsessionbaseinfo 返回空数据")

        # 确认是文章小节
        session_type = str(raw_data.get("sessionType", ""))
        if session_type != "13":
            raise RuntimeError(
                f"小节类型不匹配: sessionType={session_type}，期望文章小节（sessionType=13）"
            )

        # 2. 深拷贝并过滤只读字段
        session_info = copy.deepcopy(raw_data)
        for ro_field in _READONLY_SESSIONINFO_FIELDS:
            session_info.pop(ro_field, None)

        # 额外过滤只在修改时出现的只读字段
        session_info.pop("creatTimeShow", None)
        session_info.pop("richText", None)

        # 3. 记录原始值
        old_cover_resource_id = ""
        extend = dict(session_info.get("extend", {}) or {})
        setup = dict(session_info.get("setup", {}) or {})

        # 注意：getsessionbaseinfo 返回的 extend 只包含 textlength，
        # 封面图 resource_id 在 getSessionQuestionInfo 的 extend 中（JSON字符串）

        # 4. 应用变更
        modified_fields: list[str] = []

        if session_title is not None:
            session_info["sessionTitle"] = session_title
            modified_fields.append("session_title")

        if is_required is not None:
            session_info["is_require"] = 1 if is_required else 0
            modified_fields.append("is_required")

        # 处理文章内容变更
        if article_content is not None:
            current_multimedia_id = str(session_info.get("multimedia_id", "0"))
            if current_multimedia_id and current_multimedia_id != "0":
                try:
                    self._update_fulltext(
                        top_section_id=current_multimedia_id,
                        content=article_content,
                        ref_id="0",
                        ref_type="article",
                    )
                    logger.info("文章内容更新成功: multimedia_id=%s", current_multimedia_id)
                    modified_fields.append("article_content")
                except Exception as e:
                    logger.error("文章内容更新失败: %s", e)
                    raise RuntimeError(f"文章内容更新失败: {e}")
            else:
                # 没有现有富文本，创建新的
                try:
                    new_multimedia_id = self._create_fulltext(
                        content=article_content,
                        ref_type="article",
                    )
                    session_info["multimedia_type"] = 1
                    session_info["multimedia_id"] = new_multimedia_id
                    modified_fields.append("article_content")
                    logger.info("文章富文本新建成功: multimedia_id=%s", new_multimedia_id)
                except Exception as e:
                    logger.error("文章富文本创建失败: %s", e)
                    raise RuntimeError(f"文章富文本创建失败: {e}")

        # 处理 setup 变更
        if min_duration_seconds is not None:
            setup["vlt_min"] = min_duration_seconds
            modified_fields.append("min_duration_seconds")

        if max_duration_seconds is not None:
            setup["vlt_max"] = max_duration_seconds
            modified_fields.append("max_duration_seconds")

        if show_course_creator_info is not None:
            setup["show_course_creator_info"] = "1" if show_course_creator_info else "0"
            modified_fields.append("show_course_creator_info")

        if show_article_reading_speed is not None:
            setup["show_article_reading_speed"] = "1" if show_article_reading_speed else "0"
            modified_fields.append("show_article_reading_speed")

        if is_comment_time_visible is not None:
            setup["is_comment_time_visible"] = "1" if is_comment_time_visible else "0"
            modified_fields.append("is_comment_time_visible")

        if enable_comment is not None:
            setup["close_comment_switch"] = 0 if enable_comment else 1
            modified_fields.append("enable_comment")

        if type_name is not None:
            if type_name:
                setup["type_name"] = type_name
            else:
                setup.pop("type_name", None)
            modified_fields.append("type_name")

        session_info["setup"] = setup

        # 5. 处理封面图变更
        new_cover_resource_id = old_cover_resource_id

        if remove_cover:
            new_cover_resource_id = ""
            modified_fields.append("remove_cover")
            extend.pop("resource_id", None)
        elif cover_image_path is not None:
            new_cover_resource_id = self._upload_cover_image(
                cover_image_path=cover_image_path,
                fatal=True,
            )
            modified_fields.append("cover_image")
        elif cover_resource_id is not None:
            new_cover_resource_id = cover_resource_id
            modified_fields.append("cover_resource_id")

        if new_cover_resource_id:
            extend["resource_id"] = new_cover_resource_id

        session_info["extend"] = extend

        # 6. 处理标签变更
        if tags is not None:
            session_info["tags"] = [{"tag": str(tag)} for tag in tags]
            modified_fields.append("tags")

        # 7. 构造 session_data
        # 从 getSessionQuestionInfo 获取 questionId 和封面 resource_id
        question_id = ""
        try:
            q_resp = self.client.get(
                self.client.desktop_url("/api/session/getSessionQuestionInfo"),
                params={"session_id": session_id},
            )
            if q_resp.get("status") is True or q_resp.get("error_code") == 0:
                q_data = q_resp.get("data", [])
                if q_data and len(q_data) > 0:
                    question_id = str(q_data[0].get("id", ""))
                    # 解析 extend JSON 字符串获取旧封面 resource_id
                    q_extend_raw = q_data[0].get("extend", "")
                    if q_extend_raw:
                        try:
                            q_extend = json.loads(q_extend_raw)
                            old_cover_resource_id = q_extend.get("resource_id", "")
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            logger.warning("获取 questionId 失败: %s", e)

        # 构造 questionInfo（极简模式 + 封面变更）
        question_info: dict[str, Any] = {
            "showIndex": 0,
            "questionId": question_id,
        }
        # 如果封面有变更，需要在 extend 中设置新的 resource_id
        if new_cover_resource_id and new_cover_resource_id != old_cover_resource_id:
            question_info["extend"] = {"resource_id": new_cover_resource_id}
            logger.info(
                "文章小节封面变更: old=%s, new=%s",
                old_cover_resource_id,
                new_cover_resource_id,
            )
        elif remove_cover and old_cover_resource_id:
            # 移除封面：extend 设为空对象
            question_info["extend"] = {}
            logger.info("文章小节封面移除: old=%s", old_cover_resource_id)

        session_data = {
            "sessionInfo": session_info,
            "sectionArr": [
                {
                    "questionInfo": question_info,
                }
            ],
        }

        # 8. 调用 savesession
        logger.info(
            "保存文章小节修改: session_id=%s, modified=%s",
            session_id,
            modified_fields,
        )
        resp = self.client.post(
            self.client.desktop_url("/api/session/savesession"),
            data={
                "group_id": group_id,
                "session_id": session_id,
                "session_data": json.dumps(session_data, ensure_ascii=False),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error(
                "savesession 更新错误: %s",
                json.dumps(resp, ensure_ascii=False),
            )
            raise RuntimeError(
                f"更新文章小节失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        logger.info("文章小节修改保存成功: session_id=%s", session_id)

        # 9. 保存标签
        self._save_keywords(session_id, tags=tags)

        return {
            "session_id": session_id,
            "group_id": group_id,
            "modified_fields": modified_fields,
            "title": session_title or session_info.get("sessionTitle", ""),
            "cover_resource_id": new_cover_resource_id or None,
        }

    # ------------------------------------------------------------------
    # 图文小节（infographic / sessionType=15）
    # ------------------------------------------------------------------

    def _upload_infographic_content_images(
        self,
        content_blocks: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """上传内容中的本地图片，返回处理后的内容块.

        对于 type="image" 且 content 是本地路径的项，上传图片并替换为 URL。
        已上传的 URL 保持不变。

        Args:
            content_blocks: 原始内容块列表

        Returns:
            处理后的内容块列表
        """
        processed: list[dict[str, str]] = []
        uploader: ImageUploader | None = None

        for block in content_blocks:
            block_type = block.get("type", "").lower()
            content = block.get("content", "")

            if block_type == "image" and content:
                # 判断是否为本地路径
                is_local = (
                    content.startswith("/")
                    or content.startswith("\\\\")
                    or (len(content) > 1 and content[1] == ":")
                    or content.startswith("~")
                )
                if is_local:
                    if uploader is None:
                        uploader = ImageUploader(
                            self.client, self.client.base_url
                        )
                    try:
                        result = uploader.upload(
                            content, media_type="image"
                        )
                        processed.append({
                            "type": "image",
                            "content": result.file_url,
                            "file_size": str(result.file_size),
                        })
                        logger.info(
                            "图文内容图片上传成功: %s -> %s",
                            content, result.file_url,
                        )
                    except Exception as e:
                        logger.error(
                            "图文内容图片上传失败: %s - %s", content, e
                        )
                        raise RuntimeError(
                            f"图文内容图片上传失败: {content} - {e}"
                        ) from e
                else:
                    # 已上传的 URL
                    processed.append(block)
            else:
                processed.append(block)

        return processed

    def _call_imgtextupd(
        self,
        session_id: str,
        content_blocks: list[dict[str, str]],
        resource_imgText_id: str = "",
        existing_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """调用 imgtextupd API 保存图文内容.

        Args:
            session_id: 小节 ID
            content_blocks: 内容块列表，每项 {"type": "image"|"text", "content": "..."}
            resource_imgText_id: 图文资源 ID（修改时必需）
            existing_items: 现有内容项列表（修改时使用，用于保留 id）

        Returns:
            API 响应数据，含 "resp" 和 "resource_imgText_id"
        """
        content_arr: list[dict[str, Any]] = []
        index_str: dict[str, int] = {}
        content_map_str: dict[str, str] = {}

        # 按类型分组收集现有内容的 id（用于顺序调换时正确匹配）
        existing_img_ids: list[str] = []
        existing_txt_ids: list[str] = []
        existing_img_sizes: list[int] = []
        existing_txt_sizes: list[int] = []

        if existing_items:
            for item in existing_items:
                item_id = str(item.get("id", ""))
                item_type = item.get("type", "")
                item_size = int(item.get("file_size", 0) or 0)
                if item_type == "img" and item_id:
                    existing_img_ids.append(item_id)
                    existing_img_sizes.append(item_size)
                elif item_type == "txt" and item_id:
                    existing_txt_ids.append(item_id)
                    existing_txt_sizes.append(item_size)

        img_idx = 0
        txt_idx = 0

        for idx, block in enumerate(content_blocks):
            cid = f"c_{idx}"
            block_type = block.get("type", "").lower()
            content = block.get("content", "")
            file_size = int(block.get("file_size", 0) or 0)

            if block_type == "image":
                api_type = "img"
                # 按类型从现有图片 id 池中获取未使用的 id
                if img_idx < len(existing_img_ids):
                    server_id = existing_img_ids[img_idx]
                    file_size = existing_img_sizes[img_idx]
                    img_idx += 1
                else:
                    server_id = ""
                    file_size = 0
            else:
                api_type = "txt"
                file_size = 0
                # 按类型从现有文字 id 池中获取未使用的 id
                if txt_idx < len(existing_txt_ids):
                    server_id = existing_txt_ids[txt_idx]
                    txt_idx += 1
                else:
                    server_id = ""

            content_arr.append({
                "cid": cid,
                "id": server_id,
                "content": content,
                "file_size": file_size,
                "type": api_type,
            })
            index_str[cid] = idx + 1  # 1-based index
            content_map_str[cid] = server_id

        payload: dict[str, Any] = {
            "sessionId": session_id,
            "contentArr": json.dumps(content_arr, ensure_ascii=False),
            "indexStr": json.dumps(index_str, ensure_ascii=False),
            "contentMapStr": json.dumps(
                content_map_str, ensure_ascii=False
            ),
        }
        if resource_imgText_id:
            payload["resource_imgText_id"] = resource_imgText_id

        logger.info(
            "调用 imgtextupd: session_id=%s, blocks=%d, resource_imgText_id=%s",
            session_id, len(content_arr), resource_imgText_id or "(none)",
        )

        resp = self.client.post(
            self.client.desktop_url("/api/multimedia/imgtextupd"),
            data=payload,
        )

        # imgtextupd 响应通常无 text 内容，检查 HTTP 状态即可
        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error(
                "imgtextupd 错误: %s", json.dumps(resp, ensure_ascii=False)
            )
            raise RuntimeError(
                f"保存图文内容失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        # 尝试从响应中提取 resource_imgText_id
        returned_resource_id = ""
        if isinstance(resp, dict):
            returned_resource_id = str(resp.get("data", {}).get("resource_imgText_id", ""))
            if not returned_resource_id:
                returned_resource_id = str(resp.get("data", {}).get("id", ""))
            if not returned_resource_id:
                returned_resource_id = str(resp.get("resource_imgText_id", ""))

        logger.info(
            "imgtextupd 成功: session_id=%s, resource_imgText_id=%s",
            session_id, returned_resource_id or "(not in resp)",
        )
        return {"resp": resp, "resource_imgText_id": returned_resource_id}

    def _get_imgtextlist(
        self,
        resource_imgText_id: str,
        page: int = 1,
        size: int = 500,
    ) -> list[dict[str, Any]]:
        """获取图文内容列表.

        Args:
            resource_imgText_id: 图文资源 ID
            page: 页码
            size: 每页数量

        Returns:
            内容项列表
        """
        resp = self.client.get(
            self.client.desktop_url("/api/multimedia/getimgtextlist"),
            params={
                "resource_imgText_id": resource_imgText_id,
                "page": page,
                "size": size,
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.warning(
                "getimgtextlist 失败: %s",
                json.dumps(resp, ensure_ascii=False),
            )
            return []

        return resp.get("data", {}).get("list", [])

    def _bind_infographic_resources_v1(
        self,
        session_id: str,
        resource_imgText_id: str = "",
        cover_resource_id: str = "",
    ) -> None:
        """使用 v1 API 绑定图文资源和封面.

        Args:
            session_id: 小节 ID
            resource_imgText_id: 图文内容资源 ID
            cover_resource_id: 封面资源 ID
        """
        if resource_imgText_id:
            resp = self.client.post(
                self.client.desktop_url("/uapi/v1/resource/bind-upd"),
                data={
                    "parent_id": session_id,
                    "resource_type": "4",
                    "bind_resource_ids": json.dumps(
                        [resource_imgText_id], ensure_ascii=False
                    ),
                    "parent_type": "4",
                },
            )
            if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
                logger.warning(
                    "图文资源绑定失败: %s",
                    json.dumps(resp, ensure_ascii=False),
                )
            else:
                logger.info(
                    "图文资源绑定成功: session_id=%s, resource=%s",
                    session_id, resource_imgText_id,
                )

        if cover_resource_id:
            resp = self.client.post(
                self.client.desktop_url("/uapi/v1/resource/bind-upd"),
                data={
                    "parent_id": session_id,
                    "resource_type": "6",
                    "bind_resource_ids": json.dumps(
                        [cover_resource_id], ensure_ascii=False
                    ),
                    "parent_type": "4",
                },
            )
            if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
                logger.warning(
                    "封面资源绑定失败: %s",
                    json.dumps(resp, ensure_ascii=False),
                )
            else:
                logger.info(
                    "封面资源绑定成功: session_id=%s, cover=%s",
                    session_id, cover_resource_id,
                )

    def create_infographic_section(
        self,
        group_id: str,
        session_title: str,
        content_blocks: list[dict[str, str]],
        cover_image_path: str = "",
        cover_resource_id: str = "",
        is_required: bool = True,
        type_name: str = "",
        min_duration_seconds: int = 0,
        max_duration_seconds: int = 0,
        show_course_creator_info: bool = True,
        show_article_reading_speed: bool = True,
        is_comment_time_visible: bool = True,
        enable_comment: bool = True,
        tags: list[str] | None = None,
        sort_order: int = 0,
    ) -> dict[str, Any]:
        """在课程中创建图文类型小节.

        图文小节使用 sessionType="15"，内容通过 imgtextupd API 存储：
        1. 调用 element/save 创建小节元素（返回 session_id）
        2. 调用 savesession 保存小节设置
        3. 调用 imgtextupd 提交图文内容
        4. 调用 bind-upd(v1) 绑定图文资源和封面

        内容块格式：
        - {"type": "image", "content": "图片URL或本地路径"}
        - {"type": "text", "content": "文字内容"}

        Args:
            group_id: 课程 ID
            session_title: 小节标题
            content_blocks: 图文内容块列表（至少一项）
            cover_image_path: 封面图本地路径（jpg/png），可选
            cover_resource_id: 已上传的封面图资源 ID（与 cover_image_path 二选一）
            is_required: 是否必修（默认 True）
            type_name: 小节类型标签名称
            min_duration_seconds: 最小学习时长（秒，0=不限制）
            max_duration_seconds: 学习时长统计上限（秒，0=不限制）
            show_course_creator_info: 是否展示课程创建者信息（默认 True）
            show_article_reading_speed: 是否展示阅读速度（默认 True）
            is_comment_time_visible: 是否允许学员查看发言提交时间（默认 True）
            enable_comment: 是否开启发言区（默认 True）
            tags: 标签文本列表
            sort_order: 排序序号，0 表示自动追加

        Returns:
            包含 session_id 等信息的字典

        Raises:
            RuntimeError: 创建小节失败
            ValueError: 参数不合法
        """
        if not content_blocks:
            raise ValueError("content_blocks 不能为空，至少提供一个内容块")

        if cover_image_path and cover_resource_id:
            raise ValueError(
                "cover_image_path 和 cover_resource_id 不能同时提供，请二选一"
            )

        # 1. 上传内容中的本地图片
        processed_blocks = self._upload_infographic_content_images(
            content_blocks
        )

        # 2. 上传封面图（如有）
        actual_cover_resource_id = self._upload_cover_image(
            cover_image_path=cover_image_path or None,
        )
        if not actual_cover_resource_id:
            actual_cover_resource_id = cover_resource_id

        # 3. 调用 element/save 创建小节元素
        element_data = {
            "title": session_title,
            "is_require": 1 if is_required else 0,
            "access_permission": 1,
            "type": 15,
            "tags": [],
            "desc": "",
            "auto_check": 1,
        }

        resp = self.client.post(
            self.client.desktop_url("/uapi/v1/element/save"),
            data={
                "parent_id": group_id,
                "parent_type": "1",
                "element_data": json.dumps(
                    element_data, ensure_ascii=False
                ),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(
                f"创建图文小节失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        session_id = str(resp.get("data", {}).get("id", ""))
        if not session_id:
            # 备选：尝试从 data 的其他字段获取
            session_id = str(resp.get("data", {}).get("session_id", ""))
        if not session_id:
            session_id = str(resp.get("data", {}).get("element_id", ""))
        if not session_id:
            # 终极备选：尝试从响应顶层获取
            session_id = str(resp.get("id", ""))
        if not session_id:
            raise RuntimeError(
                f"创建图文小节成功但返回的 session_id 为空，"
                f"响应: {json.dumps(resp, ensure_ascii=False)[:500]}"
            )

        logger.info(
            "图文小节元素创建成功: session_id=%s", session_id
        )

        # 4. 构造 setup
        setup: dict[str, Any] = {
            "type_name": type_name or "",
            "is_comment_time_visible": "1" if is_comment_time_visible else "0",
            "comment_sort_type": "1",
            "vlt_min": min_duration_seconds,
            "vlt_max": max_duration_seconds,
            "show_course_creator_info": "1" if show_course_creator_info else "0",
            "show_article_reading_speed": "1" if show_article_reading_speed else "0",
            "close_comment_switch": 0 if enable_comment else 1,
            "pdf_watermark": "0",
            "create_device": "wap",
            "serverTime": int(time.time()),
        }

        # 5. 构造 sessionInfo
        session_info: dict[str, Any] = {
            "chapter_id": "0",
            "create_teacher_id": "",
            "status": "0",
            "extend": {"serverTime": int(time.time())},
            "setup": setup,
            "desc": "",
            "permission": "1",
            "access_permission": 1,
            "multimedia_id": 0,
            "multimedia_type": "0",
            "is_require": 1 if is_required else 0,
            "is_top": 0,
            "is_deleted": "0",
            "sessionId": session_id,
            "sessionTitle": session_title,
            "groupId": group_id,
            "sessionIndex": str(sort_order),
            "sessionType": "15",
            "autoCheck": 1,
            "creatTime": str(int(time.time())),
            "resultType": "0",
            "totalUserCount": 0,
            "onlineUserCount": 0,
            "richText": "",
            "studentRegFlag": "0",
            "tags": [{"tag": str(tag)} for tag in (tags or [])],
            "point_ratio": "1",
        }

        session_data = {
            "sessionInfo": session_info,
            "sectionArr": [],
        }

        # 6. 调用 savesession
        resp = self.client.post(
            self.client.desktop_url("/api/session/savesession"),
            data={
                "group_id": group_id,
                "session_id": session_id,
                "session_data": json.dumps(
                    session_data, ensure_ascii=False
                ),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error(
                "savesession 错误: %s", json.dumps(resp, ensure_ascii=False)
            )
            raise RuntimeError(
                f"保存图文小节设置失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        logger.info("图文小节设置保存成功: session_id=%s", session_id)

        # 7. 调用 imgtextupd 提交图文内容
        resource_imgText_id = ""
        try:
            imgtext_result = self._call_imgtextupd(
                session_id=session_id,
                content_blocks=processed_blocks,
            )
            resource_imgText_id = imgtext_result.get("resource_imgText_id", "")
        except Exception as e:
            logger.error("图文内容保存失败: %s", e)
            # 内容保存失败，但不回滚小节创建
            raise RuntimeError(f"图文小节已创建但内容保存失败: {e}")

        # 8. 绑定图文资源和封面
        if resource_imgText_id or actual_cover_resource_id:
            try:
                self._bind_infographic_resources_v1(
                    session_id=session_id,
                    resource_imgText_id=resource_imgText_id,
                    cover_resource_id=actual_cover_resource_id,
                )
            except Exception as e:
                logger.warning("资源绑定失败（非致命）: %s", e)

        # 9. 保存标签
        self._save_keywords(session_id, tags=(tags or []))

        return {
            "session_id": session_id,
            "group_id": group_id,
            "title": session_title,
            "content_block_count": len(processed_blocks),
            "cover_resource_id": actual_cover_resource_id or None,
            "resource_imgText_id": resource_imgText_id or None,
            "is_required": is_required,
            "type_name": type_name,
            "min_duration_seconds": min_duration_seconds,
            "max_duration_seconds": max_duration_seconds,
        }

    # ------------------------------------------------------------------
    # 创建问卷小节
    # ------------------------------------------------------------------

    def create_survey_section(
        self,
        group_id: str,
        session_title: str,
        questions: list[dict[str, Any]],
        is_required: bool = True,
        jump_button: bool = False,
        jump_url: str = "",
        jump_button_title: str = "",
        show_user_result: bool = False,
        is_show_participate_on_screen: bool = True,
        share_status: int = 1,
        submit_permission: int = 1,
        allow_modify: bool = False,
        submit_limit: str = "1",
        result_prompt: str = "感谢您的参与！",
        accept_submission_time: int = 0,
        refuse_submission_time: int = 0,
        random_option: bool = False,
        type_name: str = "",
        tags: list[str] | None = None,
        sort_order: int = 0,
    ) -> dict[str, Any]:
        """在课程中创建问卷类型小节.

        问卷小节使用 sessionType="1"，所有题目和选项通过 sectionArr 一次性提交。

        题目格式（questions 列表中每项为 dict）：

        **单选题 (type="radio")：**
        {
            "type": "radio",
            "title": "题目内容",
            "required": true,
            "options": ["选项1", "选项2", "选项3"],
            "extra_answer": {"label": "其他", "required": false},  # 可选
            "screen_order": "none"  # none|initial|fixed
        }

        **多选题 (type="checkbox")：**
        {
            "type": "checkbox",
            "title": "题目内容",
            "required": true,
            "options": ["选项1", "选项2", "选项3"],
            "extra_answer": {"label": "其他", "required": false},  # 可选
            "min_options": 1,  # 最少选几项
            "max_options": 3,  # 最多选几项
            "screen_order": "none"
        }

        **简答式填空 (type="textarea")：**
        {
            "type": "textarea",
            "title": "题目内容",
            "required": true,
            "default_answer": "默认答案"  # 可选，展示为占位提示
        }

        **量值题 (type="number")：**
        {
            "type": "number",
            "title": "评分",
            "required": true,
            "min_value": 1,
            "max_value": 5,
            "min_label": "差",
            "max_label": "好",
            "default_value": 3
        }

        **段落说明 (type="paragraph")：**
        {
            "type": "paragraph",
            "content": "<p>说明文字</p>"  # 支持HTML
        }

        **段落说明的位置：**
        段落说明可以放在 `questions` 数组的**任何位置**（题目前、题目后、或题目之间），
        用于分隔不同主题的题目或添加辅助说明。段落说明不是题目，学员不需要作答。

        示例（段落说明在题目之间）：
        ```python
        questions = [
            {"type": "radio", "title": "Q1", ...},
            {"type": "paragraph", "content": "<p>以下是一组关于满意度的问题</p>"},
            {"type": "number", "title": "Q2", ...},
            {"type": "paragraph", "content": "<p>感谢您的参与！</p>"},
        ]
        ```

        问卷设置参数：
        - share_status: 1=课程内公开, 2=企业内公开, 3=仅自己
        - submit_permission: 1=允许匿名提交, 2=必须登录后提交
        - submit_limit: "1"=最多1次, "n"=允许多次
        - allow_modify: False=不允许提交后修改, True=允许

        Args:
            group_id: 课程 ID
            session_title: 问卷小节标题
            questions: 题目列表，每项为一个 dict
            is_required: 是否必修（默认 True）
            jump_button: 提交成功后是否显示跳转按钮（默认 False）
            jump_url: 跳转按钮的目标 URL（jump_button=True 时有效）
            jump_button_title: 跳转按钮的文本（默认空）
            show_user_result: 提交后是否展示问卷结果（默认 False）
            is_show_participate_on_screen: 大屏幕是否展示参与人数（默认 True）
            share_status: 问卷访问权限（1=课程内公开, 2=企业内公开, 3=仅自己）
            submit_permission: 提交权限（1=允许匿名, 2=必须登录）
            allow_modify: 是否允许提交后修改（默认 False）
            submit_limit: 提交次数限制（"1"=1次, "n"=多次）
            result_prompt: 提交成功提示语（默认"感谢您的参与！"）
            accept_submission_time: 开始提交时间（Unix 时间戳，0=不限制）
            refuse_submission_time: 结束提交时间（Unix 时间戳，0=不限制）
            random_option: 选项是否随机展示（默认 False）
            type_name: 小节类型标签（如"问卷"、"调研"）
            tags: 标签文本列表
            sort_order: 排序序号，0 表示自动追加

        Returns:
            包含 session_id 和 question_count 等信息的字典

        Raises:
            RuntimeError: 创建小节失败
            ValueError: 参数不合法（如题目类型不支持、缺少必填字段）
        """
        if not questions:
            raise ValueError("questions 不能为空，问卷至少需要包含一个题目")

        # 生成唯一的 cid 前缀
        cid_counter = 0

        def _next_cid() -> str:
            nonlocal cid_counter
            cid = f"c_{int(time.time() * 1000)}_{cid_counter}"
            cid_counter += 1
            return cid

        section_arr: list[dict[str, Any]] = []

        for q_idx, q in enumerate(questions):
            q_type = q.get("type", "").lower()
            title = q.get("title", "")
            required = bool(q.get("required", False))
            screen_order = q.get("screen_order", "none")

            # 映射 screen_order 到 screenOrderType
            screen_order_map = {
                "none": "0",
                "initial": "1",
                "fixed": "2",
            }
            screen_order_type = screen_order_map.get(screen_order, "0")

            if q_type == "radio":
                # 单选题
                options = q.get("options", [])
                if not options:
                    raise ValueError(f"第 {q_idx + 1} 题（单选）必须提供 options")

                extra_answer = q.get("extra_answer")
                has_extra = 1 if extra_answer else 0

                answer_arr: list[dict[str, Any]] = []
                for opt in options:
                    answer_arr.append({
                        "answerContent": str(opt),
                        "answerId": "",
                        "questionId": "",
                        "type": 0,
                        "extend": {"pic_url": []},
                        "isFocus": False,
                    })
                # 添加空白选项（前端惯例）
                answer_arr.append({
                    "answerContent": "",
                    "answerId": "",
                    "questionId": "",
                    "type": 0,
                    "extend": {"pic_url": []},
                })
                # 额外答案
                if extra_answer:
                    answer_arr.append({
                        "answerContent": "",
                        "answerId": "",
                        "questionId": "",
                        "type": 1,
                        "extend": {
                            "pic_url": [],
                            "extra_required": 1 if extra_answer.get("required", False) else 0,
                            "extra_label": extra_answer.get("label", "其他"),
                        },
                    })

                question_info = {
                    "questionId": "",
                    "sessionId": "",
                    "questionTitle": title,
                    "questionIndex": "",
                    "pattern": "",
                    "required": "",
                    "creatTime": "",
                    "creatTimeShow": "",
                    "domType": "radio",
                    "totalCount": "",
                    "showType": {},
                    "showIndex": 0,
                    "setup": {
                        "required": "1" if required else "0",
                        "screenOrderType": screen_order_type,
                    },
                    "extend": {"pic_url": []},
                    "cid": _next_cid(),
                    "hasExtraAnswer": has_extra,
                }
                section_arr.append({"questionInfo": question_info, "answerArr": answer_arr})

            elif q_type == "checkbox":
                # 多选题
                options = q.get("options", [])
                if not options:
                    raise ValueError(f"第 {q_idx + 1} 题（多选）必须提供 options")

                extra_answer = q.get("extra_answer")
                has_extra = 1 if extra_answer else 0
                min_options = q.get("min_options", 0)
                max_options = q.get("max_options", 0)

                answer_arr = []
                for opt in options:
                    answer_arr.append({
                        "answerContent": str(opt),
                        "answerId": "",
                        "questionId": "",
                        "type": 0,
                        "extend": {"pic_url": []},
                        "isFocus": False,
                    })
                # 空白选项
                answer_arr.append({
                    "answerContent": "",
                    "answerId": "",
                    "questionId": "",
                    "type": 0,
                    "extend": {"pic_url": []},
                })
                # 额外答案
                if extra_answer:
                    answer_arr.append({
                        "answerContent": "",
                        "answerId": "",
                        "questionId": "",
                        "type": 1,
                        "extend": {
                            "pic_url": [],
                            "extra_required": 1 if extra_answer.get("required", False) else 0,
                            "extra_label": extra_answer.get("label", "其他"),
                        },
                    })

                setup: dict[str, Any] = {
                    "required": "1" if required else "0",
                    "screenOrderType": screen_order_type,
                }
                if min_options > 0:
                    setup["limitOptionsMin"] = min_options
                if max_options > 0:
                    setup["limitOptionsMax"] = max_options

                question_info = {
                    "questionId": "",
                    "sessionId": "",
                    "questionTitle": title,
                    "questionIndex": "",
                    "pattern": "",
                    "required": "",
                    "creatTime": "",
                    "creatTimeShow": "",
                    "domType": "checkbox",
                    "totalCount": "",
                    "showType": {},
                    "showIndex": 0,
                    "setup": setup,
                    "extend": {"pic_url": []},
                    "cid": _next_cid(),
                    "hasExtraAnswer": has_extra,
                }
                section_arr.append({"questionInfo": question_info, "answerArr": answer_arr})

            elif q_type == "textarea":
                # 简答式填空
                default_answer = q.get("default_answer", "")

                question_info = {
                    "questionId": "",
                    "sessionId": "",
                    "questionTitle": title,
                    "questionIndex": "",
                    "pattern": "",
                    "required": "",
                    "creatTime": "",
                    "creatTimeShow": "",
                    "domType": "textarea",
                    "totalCount": "",
                    "showType": {},
                    "showIndex": 0,
                    "setup": {
                        "required": "1" if required else "0",
                        "screenOrderType": screen_order_type,
                    },
                    "extend": {"pic_url": []},
                    "cid": _next_cid(),
                    "hasExtraAnswer": 0,
                }
                answer_arr = [{
                    "answerContent": default_answer,
                    "answerId": "",
                    "questionId": "",
                }]
                section_arr.append({"questionInfo": question_info, "answerArr": answer_arr})

            elif q_type == "number":
                # 量值题
                min_value = q.get("min_value", 1)
                max_value = q.get("max_value", 5)
                min_label = q.get("min_label", "")
                max_label = q.get("max_label", "")
                default_value = q.get("default_value")

                setup = {
                    "required": "1" if required else "0",
                    "screenOrderType": screen_order_type,
                }
                if default_value is not None:
                    setup["defaultValue"] = default_value

                extend: dict[str, Any] = {
                    "pic_url": [],
                    "min": min_value,
                    "max": max_value,
                }
                if min_label:
                    extend["minDesc"] = min_label
                if max_label:
                    extend["maxDesc"] = max_label

                question_info = {
                    "questionId": "",
                    "sessionId": "",
                    "questionTitle": title,
                    "questionIndex": "",
                    "pattern": "",
                    "required": "",
                    "creatTime": "",
                    "creatTimeShow": "",
                    "domType": "number",
                    "totalCount": "",
                    "showType": {},
                    "showIndex": 0,
                    "setup": setup,
                    "extend": extend,
                    "cid": _next_cid(),
                    "hasExtraAnswer": 0,
                }
                section_arr.append({"questionInfo": question_info, "answerArr": []})

            elif q_type == "paragraph":
                # 段落说明（非题目，富文本内容）
                content = q.get("content", "")
                if not content:
                    raise ValueError(f"第 {q_idx + 1} 题（段落说明）必须提供 content")

                # 创建富文本
                multimedia_id = self._create_fulltext(
                    content=content,
                    ref_type="question",
                )

                question_info = {
                    "questionId": "",
                    "sessionId": "",
                    "questionTitle": "",
                    "questionIndex": "",
                    "pattern": "",
                    "required": "",
                    "creatTime": "",
                    "creatTimeShow": "",
                    "domType": "paragraph",
                    "totalCount": "",
                    "showType": {},
                    "showIndex": 0,
                    "setup": {},
                    "extend": {},
                    "cid": _next_cid(),
                    "multimedia_id": str(multimedia_id),
                    "multimedia_weight": q_idx + 1,
                }
                section_arr.append({"questionInfo": question_info, "answerArr": []})

            else:
                raise ValueError(
                    f"第 {q_idx + 1} 题: 不支持的题目类型 '{q_type}'。"
                    f"支持: radio, checkbox, textarea, number, paragraph"
                )

        # 构造 sessionInfo.setup
        setup = {
            "jumpButton": "1" if jump_button else "0",
            "showUserResult": "1" if show_user_result else "0",
            "isShowParticipateOnScreen": "1" if is_show_participate_on_screen else "0",
            "shareStatus": share_status,
            "submitPermission": submit_permission,
            "is_allow_edit": 1,
            "allow_modify": 1 if allow_modify else 0,
            "submit_limit": str(submit_limit),
            "display_result": 1,
            "accept_submission_time": accept_submission_time,
            "refuse_submission_time": refuse_submission_time,
            "random_option": "1" if random_option else "0",
            "allow_drag_track": "0",
            "allow_adjust_speed": 1,
            "is_allow_download": 0,
            "isAllowDownload": 0,
        }
        if jump_button and jump_url:
            setup["jump_url"] = jump_url
        if jump_button and jump_button_title:
            setup["jump_button_title"] = jump_button_title
        if result_prompt:
            setup["result_prompt"] = result_prompt
        if type_name:
            setup["type_name"] = type_name

        # 构造 sessionInfo
        session_info: dict[str, Any] = {
            "autoCheck": 1,
            "creatTime": "",
            "creatTimeShow": "",
            "groupId": "",
            "onlineUserCount": "",
            "resultType": "",
            "sessionId": "",
            "sessionInUse": False,
            "sessionIndex": sort_order,
            "sessionStatus": "",
            "sessionTitle": session_title,
            "sessionType": "1",
            "teacherId": "",
            "desc": "",
            "totalCount": "",
            "studentRegFlag": False,
            "totalUserCount": "",
            "multimedia_type": 1,
            "multimedia_id": 0,
            "setup": setup,
            "extend": {},
            "point_ratio": "1",
            "is_require": 1 if is_required else 0,
            "tags": [{"tag": str(tag)} for tag in (tags or [])],
        }

        # 构造 session_data
        session_data = {
            "force_edit": 2,
            "sessionInfo": session_info,
            "sectionArr": section_arr,
        }

        # 调用 savesession
        resp = self.client.post(
            self.client.desktop_url("/api/session/savesession"),
            data={
                "group_id": group_id,
                "session_data": json.dumps(session_data, ensure_ascii=False),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error(
                "savesession 错误响应: %s",
                json.dumps(resp, ensure_ascii=False),
            )
            raise RuntimeError(
                f"保存问卷小节失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        result_data = resp.get("data", {})
        session_id = str(result_data.get("session_id", ""))

        if not session_id:
            raise RuntimeError("保存问卷小节成功但返回的 session_id 为空")

        logger.info(
            "问卷小节创建成功: session_id=%s, group_id=%s, questions=%d",
            session_id,
            group_id,
            len(questions),
        )

        # 保存标签（非致命）
        try:
            self._save_keywords(session_id, tags=(tags or []))
        except Exception as e:
            logger.warning("标签保存失败（非致命）: %s", e)

        return {
            "session_id": session_id,
            "group_id": group_id,
            "title": session_title,
            "question_count": len(questions),
            "session_type": "1",
            "is_required": is_required,
        }

    # ------------------------------------------------------------------
    # 创建考试小节
    # ------------------------------------------------------------------

    def _build_exam_section_arr(
        self,
        questions: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """根据题目列表构建考试小节的 sectionArr.

        Args:
            questions: 题目列表，每项为 dict，格式与 create_exam_section 相同

        Returns:
            (section_arr, total_score)

        Raises:
            ValueError: 参数不合法
        """
        section_arr: list[dict[str, Any]] = []
        total_score = 0
        cid_counter = 0

        def _next_cid() -> str:
            nonlocal cid_counter
            cid = f"c_{int(time.time() * 1000)}_{cid_counter}"
            cid_counter += 1
            return cid

        for q_idx, q in enumerate(questions):
            q_type = q.get("type", "").lower()
            title = q.get("title", "")
            score = int(q.get("score", 0))
            total_score += score
            explanation = q.get("explanation", "")
            difficulty = int(q.get("difficulty", 1))

            if not title:
                raise ValueError(f"第 {q_idx + 1} 题必须提供 title（题目内容）")

            if q_type == "radio":
                options = q.get("options", [])
                correct_indices = q.get("correct_indices", [])

                if not options:
                    raise ValueError(f"第 {q_idx + 1} 题（单选）必须提供 options")
                if not correct_indices:
                    raise ValueError(f"第 {q_idx + 1} 题（单选）必须提供 correct_indices")

                answer_arr: list[dict[str, Any]] = []
                for opt_idx, opt in enumerate(options):
                    answer_arr.append({
                        "answerContent": str(opt),
                        "answerId": 0,
                        "questionId": 0,
                        "type": 0,
                        "extend": {"pic_url": [], "media_url": []},
                        "isFocus": False,
                        "isRight": 1 if opt_idx in correct_indices else 0,
                    })
                answer_arr.append({
                    "answerContent": "",
                    "answerId": 0,
                    "questionId": 0,
                    "type": 0,
                    "extend": {"pic_url": [], "media_url": []},
                    "isFocus": False,
                })

                question_info = {
                    "questionId": 0,
                    "sessionId": 0,
                    "questionTitle": title,
                    "questionIndex": "",
                    "pattern": 0,
                    "required": "",
                    "creatTime": 0,
                    "creatTimeShow": "",
                    "domType": "radio",
                    "totalCount": "",
                    "showType": {},
                    "showIndex": 0,
                    "setup": {
                        "score": str(score),
                        "screenOrderType": "0",
                    },
                    "extend": {
                        "pic_url": [],
                        "media_url": [],
                    },
                    "cid": _next_cid(),
                    "level": difficulty,
                    "questionExplain": {
                        "pic_url": [],
                        "desc": explanation,
                    },
                    "score_type": 0,
                }
                section_arr.append({"questionInfo": question_info, "answerArr": answer_arr})

            elif q_type == "checkbox":
                options = q.get("options", [])
                correct_indices = q.get("correct_indices", [])
                scoring_rule = q.get("scoring_rule", "all_correct")
                partial_score = int(q.get("partial_score", 0))

                if not options:
                    raise ValueError(f"第 {q_idx + 1} 题（多选）必须提供 options")
                if not correct_indices:
                    raise ValueError(f"第 {q_idx + 1} 题（多选）必须提供 correct_indices")

                if scoring_rule == "partial":
                    score_type = 2
                    if partial_score <= 0:
                        raise ValueError(
                            f"第 {q_idx + 1} 题（多选）scoring_rule='partial' 时必须提供 partial_score（少选得分）"
                        )
                else:
                    score_type = 0
                    partial_score = 0

                answer_arr = []
                for opt_idx, opt in enumerate(options):
                    answer_arr.append({
                        "answerContent": str(opt),
                        "answerId": 0,
                        "questionId": 0,
                        "type": 0,
                        "extend": {"pic_url": [], "media_url": []},
                        "isFocus": False,
                        "isRight": 1 if opt_idx in correct_indices else 0,
                    })
                answer_arr.append({
                    "answerContent": "",
                    "answerId": 0,
                    "questionId": 0,
                    "type": 0,
                    "extend": {"pic_url": [], "media_url": []},
                    "isFocus": False,
                })

                q_setup: dict[str, Any] = {
                    "score": str(score),
                    "screenOrderType": "0",
                }
                if partial_score > 0:
                    q_setup["partial_score"] = partial_score

                question_info = {
                    "questionId": 0,
                    "sessionId": 0,
                    "questionTitle": title,
                    "questionIndex": "",
                    "pattern": 0,
                    "required": "",
                    "creatTime": 0,
                    "creatTimeShow": "",
                    "domType": "checkbox",
                    "totalCount": "",
                    "showType": {},
                    "showIndex": 0,
                    "setup": q_setup,
                    "extend": {
                        "pic_url": [],
                        "media_url": [],
                    },
                    "cid": _next_cid(),
                    "level": difficulty,
                    "questionExplain": {
                        "pic_url": [],
                        "desc": explanation,
                    },
                    "score_type": score_type,
                }
                section_arr.append({"questionInfo": question_info, "answerArr": answer_arr})

            elif q_type == "input":
                standard_answers = q.get("standard_answers", [])

                answer_arr = []
                if standard_answers:
                    for sa_idx, sa in enumerate(standard_answers):
                        if sa:
                            answer_arr.append({
                                "answerContent": str(sa),
                                "answerId": 0,
                                "questionId": 0,
                                "type": 0,
                                "extend": {"pic_url": []},
                                "isRight": 1,
                                "isFocus": sa_idx == len(standard_answers) - 1,
                            })

                answer_arr.append({
                    "answerContent": "",
                    "answerId": 0,
                    "questionId": 0,
                    "type": 0,
                    "extend": {"pic_url": []},
                    "isRight": 1 if not standard_answers else 0,
                })

                question_info = {
                    "questionId": 0,
                    "sessionId": 0,
                    "questionTitle": title,
                    "questionIndex": "",
                    "pattern": 0,
                    "required": "",
                    "creatTime": 0,
                    "creatTimeShow": "",
                    "domType": "input",
                    "totalCount": "",
                    "showType": {},
                    "showIndex": 0,
                    "setup": {
                        "score": str(score),
                        "kw": [],
                    },
                    "extend": {
                        "pic_url": [],
                        "media_url": [],
                    },
                    "cid": _next_cid(),
                    "level": difficulty,
                    "questionExplain": {
                        "pic_url": [],
                        "desc": explanation,
                    },
                    "score_type": 0,
                }
                section_arr.append({"questionInfo": question_info, "answerArr": answer_arr})

            else:
                raise ValueError(
                    f"第 {q_idx + 1} 题: 不支持的题目类型 '{q_type}'。"
                    f"支持: radio（单选）, checkbox（多选）, input（开放题）"
                )

        return section_arr, total_score

    def create_exam_section(
        self,
        group_id: str,
        session_title: str,
        questions: list[dict[str, Any]],
        description: str = "",
        exam_duration_seconds: int = 0,
        quiz_count_limit: int = 0,
        quiz_pass_mark: int = 0,
        random_option: bool = False,
        show_user_result: bool = True,
        submit_one_by_one: bool = False,
        accept_submission_time: int = 0,
        refuse_submission_time: int = 0,
        is_required: bool = True,
        type_name: str = "",
        tags: list[str] | None = None,
        sort_order: int = 0,
        question_show_mode: str = "0",
        allow_answer_type: str = "1",
        exam_result_setting: str = "0",
        switch_window_limit: int = 0,
        quiz_completion_condition: str = "0",
        share_status: int = 1,
        submit_permission: int = 1,
        show_answer_after_submit: bool = False,
        allow_add_question_collection: bool = True,
        is_show_quiz_ranking: bool = True,
        is_answer_paste: bool = True,
        quiz_cover_tips_type: str = "1",
        quiz_cover_tips_content: str = "",
        point_ratio: int = 1,
        is_set_quiz_cover: bool = True,
        jump_button: bool = False,
        jump_url: str = "",
        jump_button_title: str = "",
        result_prompt: str = "",
        show_user_result_mode: str | None = None,
        display_score: bool = True,
    ) -> dict[str, Any]:
        """在课程中创建考试类型小节.

        考试小节使用 sessionType="10"，调用 /megrez/exam/v1/saveExam。

        题目格式（questions 列表中每项为 dict）：

        **单选题 (type="radio")：**
        {
            "type": "radio",
            "title": "题目内容",
            "score": 5,
            "options": ["选项A", "选项B", "选项C"],
            "correct_indices": [2],          # 正确选项的索引（0-based）
            "explanation": "答案说明",       # 可选
            "difficulty": 1,                 # 1=低, 2=中, 3=高，可选，默认1
        }

        **多选题 - 全部正确才得分 (type="checkbox")：**
        {
            "type": "checkbox",
            "title": "题目内容",
            "score": 7,
            "options": ["选项A", "选项B", "选项C", "选项D"],
            "correct_indices": [0, 1, 2, 3],
            "explanation": "答案说明",
            "difficulty": 3,
            "scoring_rule": "all_correct",   # 全部正确才得分（默认）
        }

        **多选题 - 部分正确得分 (type="checkbox")：**
        {
            "type": "checkbox",
            "title": "题目内容",
            "score": 10,
            "options": ["选项A", "选项B", "选项C", "选项D", "选项E"],
            "correct_indices": [0, 1, 2, 3],
            "explanation": "答案说明",
            "difficulty": 2,
            "scoring_rule": "partial",       # 部分正确得分
            "partial_score": 6,              # 少选得6分
        }

        **开放题 (type="input")：**
        {
            "type": "input",
            "title": "题目内容",
            "score": 10,
            "explanation": "答案说明",
            "difficulty": 3,
            "standard_answers": ["标准答案1", "标准答案2"],  # 可选
        }

        开放题标准答案说明：
        - 设置 standard_answers 时，学员提交答案与任一标准答案一致则自动得分
        - 不设置 standard_answers（空列表或不传）时，学员提交后不会立即得分，需 teacher 手动评分

        Args:
            group_id: 课程 ID
            session_title: 考试小节标题
            questions: 题目列表
            description: 考试说明/描述（学员进入考试前展示）
            exam_duration_seconds: 考试时长（秒），0=不限时
            quiz_count_limit: 考试次数限制，0=不限次数
            quiz_pass_mark: 及格线（分），0=不设及格线
            random_option: 是否随机展示选项
            show_user_result: 是否向学员展示成绩（布尔简写）。True="1"正确答案, False="0"已提交答案
            show_user_result_mode: 提交后展示内容的精确模式。"0"=已提交答案, "1"=正确答案, "2"=不展示答案, "3"=展示对错不展示答案。None 时使用 show_user_result
            display_score: 是否向学员展示考试分数，True=展示(默认), False=不展示
            submit_one_by_one: 是否逐题提交（True=逐题提交，False=整卷提交）
            accept_submission_time: 开始接受提交时间（Unix 时间戳，0=不限制）
            refuse_submission_time: 截止提交时间（Unix 时间戳，0=不限制）
            is_required: 是否必修（默认 True）
            type_name: 小节类型标签
            tags: 标签文本列表
            sort_order: 排序序号，0 表示自动追加到末尾
            question_show_mode: 展示样式，"0"=一页式(默认)，"1"=逐题式
            allow_answer_type: 开放式问题提交格式，"1"=文字+图片(默认)，"0"=仅文字
            exam_result_setting: 成绩设置，"0"=最后一次提交为准(默认)
            switch_window_limit: 防切屏次数，0=不设置(默认)
            quiz_completion_condition: 完成条件，"0"=不设置(默认)
            share_status: 访问权限，1=课程内公开(默认)，2=企业内公开，3=仅自己
            submit_permission: 提交权限，1=课程内学员(默认)
            show_answer_after_submit: 提交后展示正确答案，False=不展示(默认)，True=展示
            allow_add_question_collection: 允许将题目加入考题本，True=允许(默认)，False=不允许
            is_show_quiz_ranking: 提交后展示考试排行榜，True=展示(默认)，False=不展示
            is_answer_paste: 回答开放式问题是否允许粘贴，True=允许(默认)，False=不允许
            quiz_cover_tips_type: 封面提示类型，"1"=自动设置(默认)，"0"=手动设置
            quiz_cover_tips_content: 封面提示内容。quiz_cover_tips_type="1"时为空则自动生成
            point_ratio: 小节基本积分倍率，默认 1
            is_set_quiz_cover: 是否设置考试封面，True=设置(默认)，False=不设置
            jump_button: 提交成功页是否显示跳转按钮，False=不跳转(默认)，True=显示
            jump_url: 跳转按钮的目标 URL（jump_button=True 时有效）
            jump_button_title: 跳转按钮的文本（jump_button=True 时有效）
            result_prompt: 提交成功提示语，默认"提交成功！"

        Returns:
            包含 session_id、group_id、title、question_count 等信息的字典

        Raises:
            RuntimeError: 创建小节失败
            ValueError: 参数不合法
        """
        if not questions:
            raise ValueError("questions 不能为空，考试至少需要包含一个题目")

        # 1. 创建富文本内容（考试说明）
        if description:
            content_html = f"<p>{description}</p>"
        else:
            content_html = f"<p>{session_title}</p>"

        multimedia_id = self._create_fulltext(
            content=content_html,
            ref_type="",
        )

        # _create_fulltext 已经创建了包含 description 的富文本内容，
        # 无需额外调用 _update_fulltext

        # 2. 构建 sectionArr
        section_arr, total_score = self._build_exam_section_arr(questions)

        # 3. 构造封面提示内容
        if quiz_cover_tips_type == "0" and quiz_cover_tips_content:
            # 手动设置封面提示
            quiz_cover_tips = quiz_cover_tips_content
        else:
            # 自动生成封面提示
            quiz_cover_tips = f"本次考试共有{len(questions)}道题，满分{total_score}分。"
            if quiz_count_limit > 0:
                quiz_cover_tips += f"每人有{quiz_count_limit}次考试机会。"
            else:
                quiz_cover_tips += "考试次数不限。"
            if exam_duration_seconds > 0:
                quiz_cover_tips += f"考试时长{exam_duration_seconds // 60}分钟。"
            # 多选题特殊说明
            for q_idx, q in enumerate(questions):
                if q.get("type", "").lower() == "checkbox":
                    scoring_rule = q.get("scoring_rule", "all_correct")
                    if scoring_rule == "all_correct":
                        quiz_cover_tips += f"第{q_idx + 1}题为多选题，全部正确才得分。"
                    elif scoring_rule == "partial":
                        partial_score = q.get("partial_score", 0)
                        quiz_cover_tips += (
                            f"第{q_idx + 1}题为多选题，全部正确得满分，"
                            f"少选得{partial_score}分，错选、多选、不选均不得分。"
                        )
            if quiz_count_limit > 0:
                quiz_cover_tips += '点击"开始考试"按钮，立即开始考试，此时将会使用1次考试机会。'

        # 4. 构造 sessionInfo.setup
        # isQuizCountLimit: "0"=不限定, "1"=1次, "2"=限定N次(quizCountLimit)
        if quiz_count_limit == 0:
            is_quiz_count_limit_val = "0"
        elif quiz_count_limit == 1:
            is_quiz_count_limit_val = "1"
        else:
            is_quiz_count_limit_val = "2"

        # showUserResult: "0"=已提交答案, "1"=正确答案, "2"=不展示答案, "3"=展示对错不展示答案
        if show_user_result_mode is not None:
            show_user_result_val = show_user_result_mode
        else:
            show_user_result_val = "1" if show_user_result else "0"

        setup: dict[str, Any] = {
            "examDuration": exam_duration_seconds,
            "randomOption": "1" if random_option else "0",
            "isQuizCountLimit": is_quiz_count_limit_val,
            "isExamDurationLimit": "1" if exam_duration_seconds > 0 else "0",
            "quizPassMark": 1 if quiz_pass_mark > 0 else 0,
            "quizPassMarkScore": quiz_pass_mark,
            "jumpButton": "1" if jump_button else "0",
            "showUserResult": show_user_result_val,
            "allow_add_question_collection": "1" if allow_add_question_collection else "0",
            "shareStatus": share_status,
            "submitOneByOne": 1 if submit_one_by_one else 0,
            "submitPermission": submit_permission,
            "allowAnswerType": allow_answer_type,
            "isSetQuizCover": "1" if is_set_quiz_cover else "0",
            "quizCoverTipsType": quiz_cover_tips_type,
            "quizCoverTipsContent": quiz_cover_tips,
            "accept_submission_time": accept_submission_time,
            "refuse_submission_time": refuse_submission_time,
            "display_score_to_student": "1" if display_score else "0",
            "show_answer_after_last_submit": "1" if show_answer_after_submit else "0",
            "allow_drag_track": "1",
            "allow_adjust_speed": 1,
            "is_allow_download": 0,
            "isAllowDownload": 0,
            "checkbox_share_scoring_rule": "0",
            "checkbox_share_scoring_ratio": "1,2",
            "quizCountLimit": quiz_count_limit,
            "questionShowMode": question_show_mode,
            "exam_result_setting": exam_result_setting,
            "quizCompletionCondition": quiz_completion_condition,
            "isShowQuizRanking": "1" if is_show_quiz_ranking else "0",
            "is_answer_paste": "1" if is_answer_paste else "0",
            "switch_window_limit": switch_window_limit,
        }
        if result_prompt:
            setup["result_prompt"] = result_prompt
        if type_name:
            setup["type_name"] = type_name
        if jump_button and jump_url:
            setup["jump_url"] = jump_url
        if jump_button and jump_button_title:
            setup["jump_button_title"] = jump_button_title

        # 5. 构造 sessionInfo
        session_info: dict[str, Any] = {
            "autoCheck": 1,
            "creatTime": "",
            "creatTimeShow": "",
            "groupId": "",
            "onlineUserCount": "",
            "resultType": "",
            "sessionId": "",
            "sessionInUse": False,
            "sessionIndex": sort_order,
            "sessionStatus": "",
            "sessionTitle": session_title,
            "sessionType": "10",
            "teacherId": "",
            "desc": "",
            "totalCount": "",
            "studentRegFlag": False,
            "totalUserCount": "",
            "multimedia_type": 1,
            "multimedia_id": int(multimedia_id),
            "setup": setup,
            "extend": {},
            "point_ratio": point_ratio,
            "is_require": 1 if is_required else 0,
            "access_permission": share_status,
            "tags": [{"tag": str(tag)} for tag in (tags or [])],
        }

        # 6. 构造 session_data
        session_data = {
            "importMark": "",
            "sessionInfo": session_info,
            "sectionArr": section_arr,
            "questionRules": {},
            "questionBankQuestionRules": {},
        }

        # 7. 调用 saveExam
        resp = self.client.post(
            self.client.desktop_url("/megrez/exam/v1/saveExam"),
            data={
                "group_id": group_id,
                "session_data": json.dumps(session_data, ensure_ascii=False),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error(
                "saveExam 错误响应: %s",
                json.dumps(resp, ensure_ascii=False),
            )
            raise RuntimeError(
                f"保存考试小节失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        result_data = resp.get("data", {})
        result_session_id = str(result_data.get("session_id", ""))

        if not result_session_id:
            raise RuntimeError("保存考试小节成功但返回的 session_id 为空")

        logger.info(
            "考试小节创建成功: session_id=%s, group_id=%s, questions=%d, total_score=%d",
            result_session_id,
            group_id,
            len(questions),
            total_score,
        )

        # 保存标签（非致命）
        try:
            self._save_keywords(result_session_id, tags=(tags or []))
        except Exception as e:
            logger.warning("标签保存失败（非致命）: %s", e)

        return {
            "session_id": result_session_id,
            "group_id": group_id,
            "title": session_title,
            "question_count": len(questions),
            "total_score": total_score,
            "session_type": "10",
            "is_required": is_required,
        }

    # ------------------------------------------------------------------
    # 更新考试小节
    # ------------------------------------------------------------------

    def update_exam_section(
        self,
        group_id: str,
        session_id: str,
        session_title: str | None = None,
        questions: list[dict[str, Any]] | None = None,
        description: str | None = None,
        exam_duration_seconds: int | None = None,
        quiz_count_limit: int | None = None,
        quiz_pass_mark: int | None = None,
        random_option: bool | None = None,
        show_user_result: bool | None = None,
        submit_one_by_one: bool | None = None,
        accept_submission_time: int | None = None,
        refuse_submission_time: int | None = None,
        is_required: bool | None = None,
        type_name: str | None = None,
        tags: list[str] | None = None,
        question_show_mode: str | None = None,
        allow_answer_type: str | None = None,
        exam_result_setting: str | None = None,
        switch_window_limit: int | None = None,
        quiz_completion_condition: str | None = None,
        share_status: int | None = None,
        submit_permission: int | None = None,
        show_answer_after_submit: bool | None = None,
        allow_add_question_collection: bool | None = None,
        is_show_quiz_ranking: bool | None = None,
        is_answer_paste: bool | None = None,
        quiz_cover_tips_type: str | None = None,
        quiz_cover_tips_content: str | None = None,
        point_ratio: int | None = None,
        is_set_quiz_cover: bool | None = None,
        jump_button: bool | None = None,
        jump_url: str | None = None,
        jump_button_title: str | None = None,
        result_prompt: str | None = None,
        show_user_result_mode: str | None = None,
        display_score: bool | None = None,
    ) -> dict[str, Any]:
        """更新课程中已有的考试类型小节.

        采用"先获取完整数据，再应用变更"的模式：
        1. 调用 getsessionInfo 获取现有小节最新完整数据
        2. 过滤只读字段（统计数据等）
        3. 应用用户传入的变更（只改提供的字段，None 表示不修改）
        4. 调用 saveExam 提交完整数据（必须包含 session_id）

        Args:
            group_id: 课程 ID
            session_id: 小节 ID
            session_title: 新小节标题，None 表示不修改
            questions: 新的题目列表（完整列表，按目标顺序排列），None 表示不修改题目
            description: 考试说明/描述，None 表示不修改
            exam_duration_seconds: 考试时长（秒），0=不限时，None 表示不修改
            quiz_count_limit: 考试次数限制，0=不限次数，None 表示不修改
            quiz_pass_mark: 及格线（分），0=不设及格线，None 表示不修改
            random_option: 是否随机展示选项，None 表示不修改
            show_user_result: 是否向学员展示成绩（布尔简写），None 表示不修改
            show_user_result_mode: 提交后展示内容精确模式。"0"=已提交答案, "1"=正确答案, "2"=不展示答案, "3"=展示对错不展示答案。None 表示不修改
            display_score: 是否向学员展示考试分数，None 表示不修改
            submit_one_by_one: 是否逐题提交，None 表示不修改
            accept_submission_time: 开始接受提交时间（Unix 时间戳），None 表示不修改
            refuse_submission_time: 截止提交时间（Unix 时间戳），None 表示不修改
            is_required: 是否必修，None 表示不修改
            type_name: 小节类型标签，None 表示不修改
            tags: 标签文本列表，None 表示不修改
            question_show_mode: 展示样式，"0"=一页式，"1"=逐题式，None 表示不修改
            allow_answer_type: 开放式问题提交格式，"1"=文字+图片，"0"=仅文字，None 表示不修改
            exam_result_setting: 成绩设置，"0"=最后一次提交为准，"1"=最高分为准，None 表示不修改
            switch_window_limit: 防切屏次数，0=不设置，None 表示不修改
            quiz_completion_condition: 完成条件，"0"=不设置，"1"=考试成绩达到及格分，None 表示不修改
            share_status: 访问权限，1=课程内公开，2=企业内公开，0=关闭，None 表示不修改
            submit_permission: 提交权限，1=课程内学员，None 表示不修改
            show_answer_after_submit: 提交后展示正确答案，None 表示不修改
            allow_add_question_collection: 允许将题目加入考题本，None 表示不修改
            is_show_quiz_ranking: 提交后展示考试排行榜，None 表示不修改
            is_answer_paste: 回答开放式问题是否允许粘贴，None 表示不修改
            quiz_cover_tips_type: 封面提示类型，"1"=自动设置，"0"=手动设置，None 表示不修改
            quiz_cover_tips_content: 封面提示内容，None 表示不修改
            point_ratio: 小节基本积分倍率，None 表示不修改
            is_set_quiz_cover: 是否设置考试封面，None 表示不修改
            jump_button: 提交成功页是否显示跳转按钮，None 表示不修改
            jump_url: 跳转按钮的目标 URL，None 表示不修改
            jump_button_title: 跳转按钮的文本，None 表示不修改
            result_prompt: 提交成功提示语，None 表示不修改

        Returns:
            包含 session_id 和 changes 列表的字典

        Raises:
            RuntimeError: 更新失败
        """
        # 1. 获取现有小节最新完整数据
        existing = self._get_session_detail(session_id)
        session_info = existing.get("sessionInfo", {})
        existing_sections = existing.get("sectionArr", [])

        # 2. 深拷贝并过滤只读字段
        updated_info = copy.deepcopy(session_info)
        for ro_field in _READONLY_SESSIONINFO_FIELDS:
            updated_info.pop(ro_field, None)

        # 额外过滤考试小节特有的只读字段
        exam_readonly = {
            "pass_mark_score", "pass_mark_count", "pass_mark_rate",
            "highest_score", "average_score", "lowest_score", "full_marks",
            "has_objective_question", "has_subjective_question",
            "question_dom", "rank_count", "submitUserCount",
            "templateId", "scenario", "is_show_exam_ranking",
            "questionBankIds", "question_bank_random_mode",
            "questionBankRandomMode", "exam_ranking_share_qrc",
            "exam_ranking_share_url",
        }
        for ro_field in exam_readonly:
            updated_info.pop(ro_field, None)

        changes: list[str] = []
        setup = updated_info.get("setup", {})

        # Helper: 同时更新 snake_case 和 camelCase 版本的字段
        def _update_setup_pair(sc_key: str, cc_key: str, value: Any) -> None:
            """同时更新 snake_case 和 camelCase 版本的 setup 字段."""
            setup[sc_key] = value
            if cc_key != sc_key:
                setup[cc_key] = value

        # 3. 应用 session 级别的变更
        if session_title is not None and session_title != updated_info.get("sessionTitle"):
            updated_info["sessionTitle"] = session_title
            changes.append(f"sessionTitle: {session_title}")

        if is_required is not None:
            new_val = 1 if is_required else 0
            if new_val != updated_info.get("is_require"):
                updated_info["is_require"] = new_val
                changes.append(f"is_require: {is_required}")

        if point_ratio is not None and point_ratio != updated_info.get("point_ratio"):
            updated_info["point_ratio"] = point_ratio
            changes.append(f"point_ratio: {point_ratio}")

        if type_name is not None:
            _update_setup_pair("type_name", "typeName", type_name)
            changes.append(f"type_name: {type_name}")

        # 4. 应用 setup 级别的变更
        if exam_duration_seconds is not None:
            _update_setup_pair("exam_duration", "examDuration", exam_duration_seconds)
            _update_setup_pair("is_exam_duration_limit", "isExamDurationLimit", "1" if exam_duration_seconds > 0 else "0")
            changes.append(f"exam_duration_seconds: {exam_duration_seconds}")

        if quiz_count_limit is not None:
            _update_setup_pair("quiz_count_limit", "quizCountLimit", quiz_count_limit)
            if quiz_count_limit == 0:
                is_qcl_val = "0"
            elif quiz_count_limit == 1:
                is_qcl_val = "1"
            else:
                is_qcl_val = "2"
            _update_setup_pair("is_quiz_count_limit", "isQuizCountLimit", is_qcl_val)
            changes.append(f"quiz_count_limit: {quiz_count_limit}")

        if quiz_pass_mark is not None:
            _update_setup_pair("quiz_pass_mark", "quizPassMark", 1 if quiz_pass_mark > 0 else 0)
            _update_setup_pair("quiz_pass_mark_score", "quizPassMarkScore", quiz_pass_mark)
            changes.append(f"quiz_pass_mark: {quiz_pass_mark}")

        if random_option is not None:
            val = "1" if random_option else "0"
            _update_setup_pair("random_option", "randomOption", val)
            changes.append(f"random_option: {random_option}")

        if show_user_result is not None:
            val = "1" if show_user_result else "0"
            _update_setup_pair("show_user_result", "showUserResult", val)
            changes.append(f"show_user_result: {show_user_result}")

        if show_user_result_mode is not None:
            _update_setup_pair("show_user_result", "showUserResult", show_user_result_mode)
            changes.append(f"show_user_result_mode: {show_user_result_mode}")

        if display_score is not None:
            val = "1" if display_score else "0"
            _update_setup_pair("display_score_to_student", "displayScoreToStudent", val)
            changes.append(f"display_score: {display_score}")

        if submit_one_by_one is not None:
            val = 1 if submit_one_by_one else 0
            _update_setup_pair("submit_one_by_one", "submitOneByOne", val)
            changes.append(f"submit_one_by_one: {submit_one_by_one}")

        if accept_submission_time is not None:
            _update_setup_pair("accept_submission_time", "acceptSubmissionTime", accept_submission_time)
            changes.append(f"accept_submission_time: {accept_submission_time}")

        if refuse_submission_time is not None:
            _update_setup_pair("refuse_submission_time", "refuseSubmissionTime", refuse_submission_time)
            changes.append(f"refuse_submission_time: {refuse_submission_time}")

        if question_show_mode is not None:
            _update_setup_pair("question_show_mode", "questionShowMode", question_show_mode)
            changes.append(f"question_show_mode: {question_show_mode}")

        if allow_answer_type is not None:
            _update_setup_pair("allow_answer_type", "allowAnswerType", allow_answer_type)
            changes.append(f"allow_answer_type: {allow_answer_type}")

        if exam_result_setting is not None:
            _update_setup_pair("exam_result_setting", "examResultSetting", exam_result_setting)
            changes.append(f"exam_result_setting: {exam_result_setting}")

        if switch_window_limit is not None:
            _update_setup_pair("switch_window_limit", "switchWindowLimit", switch_window_limit)
            changes.append(f"switch_window_limit: {switch_window_limit}")

        if quiz_completion_condition is not None:
            _update_setup_pair("quiz_completion_condition", "quizCompletionCondition", quiz_completion_condition)
            _update_setup_pair("quiz_completed_condition", "quizCompletedCondition", quiz_completion_condition)
            changes.append(f"quiz_completion_condition: {quiz_completion_condition}")

        if share_status is not None:
            _update_setup_pair("share_status", "shareStatus", share_status)
            updated_info["access_permission"] = share_status
            changes.append(f"share_status: {share_status}")

        if submit_permission is not None:
            _update_setup_pair("submit_permission", "submitPermission", submit_permission)
            changes.append(f"submit_permission: {submit_permission}")

        if show_answer_after_submit is not None:
            val = "1" if show_answer_after_submit else "0"
            _update_setup_pair("show_answer_after_last_submit", "showAnswerAfterLastSubmit", val)
            changes.append(f"show_answer_after_submit: {show_answer_after_submit}")

        if allow_add_question_collection is not None:
            val = "1" if allow_add_question_collection else "0"
            _update_setup_pair("allow_add_question_collection", "allowAddQuestionCollection", val)
            changes.append(f"allow_add_question_collection: {allow_add_question_collection}")

        if is_show_quiz_ranking is not None:
            val = "1" if is_show_quiz_ranking else "0"
            _update_setup_pair("is_show_quiz_ranking", "isShowQuizRanking", val)
            changes.append(f"is_show_quiz_ranking: {is_show_quiz_ranking}")

        if is_answer_paste is not None:
            val = "1" if is_answer_paste else "0"
            _update_setup_pair("is_answer_paste", "isAnswerPaste", val)
            changes.append(f"is_answer_paste: {is_answer_paste}")

        if quiz_cover_tips_type is not None:
            _update_setup_pair("quiz_cover_tips_type", "quizCoverTipsType", quiz_cover_tips_type)
            changes.append(f"quiz_cover_tips_type: {quiz_cover_tips_type}")

        if quiz_cover_tips_content is not None:
            _update_setup_pair("quiz_cover_tips_content", "quizCoverTipsContent", quiz_cover_tips_content)
            changes.append("quiz_cover_tips_content: ...")

        if is_set_quiz_cover is not None:
            val = "1" if is_set_quiz_cover else "0"
            _update_setup_pair("is_set_quiz_cover", "isSetQuizCover", val)
            changes.append(f"is_set_quiz_cover: {is_set_quiz_cover}")

        if jump_button is not None:
            val = "1" if jump_button else "0"
            _update_setup_pair("jump_button", "jumpButton", val)
            changes.append(f"jump_button: {jump_button}")

        if jump_url is not None:
            _update_setup_pair("jump_url", "jumpUrl", jump_url)
            changes.append(f"jump_url: {jump_url}")

        if jump_button_title is not None:
            _update_setup_pair("jump_button_title", "jumpButtonTitle", jump_button_title)
            changes.append(f"jump_button_title: {jump_button_title}")

        if result_prompt is not None:
            setup["result_prompt"] = result_prompt
            changes.append(f"result_prompt: {result_prompt}")

        # 5. 处理 description 变更
        if description is not None:
            multimedia_id = updated_info.get("multimedia_id")
            if multimedia_id:
                self._update_fulltext(
                    top_section_id=str(multimedia_id),
                    content=f"<p>{description}</p>",
                )
                changes.append("description: updated")

        # 6. 处理题目更新（如提供）
        section_arr = existing_sections
        if questions is not None:
            # 使用 create_exam_section 中的题目构建逻辑
            section_arr = self._build_exam_section_arr(questions)
            changes.append(f"questions: {len(questions)} questions updated")

        # 7. 构建 session_data 并调用 saveExam
        session_data = {
            "sessionInfo": updated_info,
            "sectionArr": section_arr,
            "questionRules": {},
            "questionBankQuestionRules": {},
        }

        resp = self.client.post(
            self.client.desktop_url("/megrez/exam/v1/saveExam"),
            data={
                "group_id": group_id,
                "session_id": session_id,
                "session_data": json.dumps(session_data, ensure_ascii=False),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error(
                "saveExam 错误响应: %s",
                json.dumps(resp, ensure_ascii=False),
            )
            raise RuntimeError(
                f"更新考试小节失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        # 8. 保存标签（非致命）
        if tags is not None:
            try:
                self._save_keywords(session_id, tags=tags)
                changes.append(f"tags: {tags}")
            except Exception as e:
                logger.warning("标签保存失败（非致命）: %s", e)

        logger.info(
            "考试小节更新成功: session_id=%s, changes=%s",
            session_id,
            changes,
        )

        return {
            "session_id": session_id,
            "group_id": group_id,
            "changes": changes,
        }

    # ------------------------------------------------------------------
    # 更新问卷小节
    # ------------------------------------------------------------------

    def update_survey_section(
        self,
        group_id: str,
        session_id: str,
        questions: list[dict[str, Any]] | None = None,
        session_title: str | None = None,
        desc: str | None = None,
        is_required: bool | None = None,
        jump_button: bool | None = None,
        jump_url: str | None = None,
        jump_button_title: str | None = None,
        show_user_result: bool | None = None,
        is_show_participate_on_screen: bool | None = None,
        share_status: int | str | None = None,
        submit_permission: int | str | None = None,
        allow_modify: bool | None = None,
        submit_limit: int | str | None = None,
        result_prompt: str | None = None,
        accept_submission_time: int | str | None = None,
        refuse_submission_time: int | str | None = None,
        random_option: bool | None = None,
        type_name: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """更新课程中已有的问卷类型小节.

        采用"先获取完整数据，再应用变更"的模式：
        1. 调用 getsessionInfo 获取现有小节最新完整数据（含 questionId、answerId、multimedia_id）
        2. 应用用户传入的变更（只改提供的字段）
        3. 按位置匹配新旧题目，保留/更新/删除/新增
        4. 调用 savesession 提交完整数据

        **题目匹配规则：**
        - `questions` 数组按**索引位置**与现有 sectionArr 匹配
        - 相同位置且 domType 相同 → 保留原有 questionId/answerId，更新其他字段
        - domType 不同 → 旧题目标记删除，新题目作为新增
        - 新数组长度超过旧数组 → 超出部分作为新增
        - 新数组长度短于旧数组 → 缺少部分标记删除

        **段落说明：**
        - 保留原有 `multimedia_id` 和 `questionId`
        - 如 `content` 变化，更新 `desc` 字段
        - 新增段落说明会创建新的富文本容器

        Args:
            group_id: 课程 ID
            session_id: 小节 ID
            questions: 新的题目列表（完整列表，按目标顺序排列），None 表示不修改题目
            session_title: 新小节标题，None 表示不修改
            desc: 小节描述/说明（问卷说明，学员进入问卷时展示），None 表示不修改
            is_required: 是否必修，None 表示不修改
            jump_button: 提交成功后是否显示跳转按钮，None 表示不修改
            jump_url: 跳转按钮目标 URL，None 表示不修改
            jump_button_title: 跳转按钮文本，None 表示不修改
            show_user_result: 提交后是否展示问卷结果，None 表示不修改
            is_show_participate_on_screen: 大屏幕是否展示参与人数，None 表示不修改
            share_status: 问卷访问权限（1=课程内公开, 2=企业内公开, 3=仅自己/关闭），None 表示不修改
            submit_permission: 提交权限（3=不允许匿名/必须登录, 4=允许匿名），None 表示不修改
            allow_modify: 是否允许提交后修改，None 表示不修改
            submit_limit: 提交次数限制（1=1次, 0或n=不限/n次），None 表示不修改
            result_prompt: 提交成功提示语，None 表示不修改
            accept_submission_time: 开始提交时间（Unix时间戳秒数或datetime字符串），None 表示不修改
            refuse_submission_time: 结束提交时间（Unix时间戳秒数或datetime字符串），None 表示不修改
            random_option: 选项是否随机展示，None 表示不修改
            type_name: 小节类型标签，None 表示不修改
            tags: 标签列表，None 表示不修改

        Returns:
            包含 session_id 和 changes 列表的字典

        Raises:
            RuntimeError: 更新失败
        """
        # 1. 获取现有小节最新完整数据
        existing = self._get_session_detail(session_id)
        session_info = existing.get("sessionInfo", {})
        existing_sections = existing.get("sectionArr", [])

        # 2. 深拷贝并过滤只读字段
        updated_session_info = copy.deepcopy(session_info)
        for ro_field in _READONLY_SESSIONINFO_FIELDS:
            updated_session_info.pop(ro_field, None)

        changes: dict[str, Any] = {}
        new_section_arr: list[dict[str, Any]] = []
        multimedia_arr: list[dict[str, Any]] = []
        cid_counter = 0

        def _next_cid() -> str:
            nonlocal cid_counter
            cid = f"c_{int(time.time() * 1000)}_{cid_counter}"
            cid_counter += 1
            return cid

        # 3. 处理题目更新
        # 收集需要删除的题目 ID（savesession 只负责增改，真正的删除需要
        # 单独调用 /ajax/e_deleteQuestion——HAR 逆向工程关键发现）
        deleted_question_ids: list[str] = []
        section_elements: list[tuple[int, dict[str, Any]]] = []
        if questions is not None:
            for idx, q in enumerate(questions):
                q_type = q.get("type", "").lower()
                title = q.get("title", "")
                required = bool(q.get("required", False))
                screen_order = q.get("screen_order", "none")

                screen_order_map = {"none": "0", "initial": "1", "fixed": "2"}
                screen_order_type = screen_order_map.get(screen_order, "0")

                # 尝试匹配现有题目（按位置）
                existing_section = existing_sections[idx] if idx < len(existing_sections) else None
                existing_qinfo = existing_section.get("questionInfo", {}) if existing_section else {}
                existing_dom_type = existing_qinfo.get("domType", "")
                existing_qid = str(existing_qinfo.get("questionId", ""))
                existing_answers = existing_section.get("answerArr", []) if existing_section else []

                # 如果 domType 不同，旧题目删除，新题目新增
                preserve_id = False
                if existing_dom_type and existing_dom_type == q_type:
                    preserve_id = True
                elif existing_dom_type:
                    deleted_question_ids.append(existing_qid)
                    existing_qid = ""  # 新题目没有ID
                    existing_answers = []

                if q_type == "radio":
                    options = q.get("options", [])
                    if not options:
                        raise ValueError(f"第 {idx + 1} 题（单选）必须提供 options")

                    extra_answer = q.get("extra_answer")
                    has_extra = 1 if extra_answer else 0

                    # 构建 answerArr
                    answer_arr: list[dict[str, Any]] = []
                    for opt_idx, opt in enumerate(options):
                        # 尝试保留旧 answerId
                        old_answer = existing_answers[opt_idx] if opt_idx < len(existing_answers) else {}
                        answer_arr.append({
                            "answerId": str(old_answer.get("answerId", "")) if old_answer else "",
                            "answerIdx": opt_idx + 1,
                            "questionId": existing_qid if preserve_id else "",
                            "answerContent": str(opt),
                            "isRight": "0",
                            "extend": {"pic_url": []},
                            "type": 0,
                            "isFocus": False,
                        })
                    # 空白选项（必须有，与前端约定一致）
                    answer_arr.append({
                        "answerContent": "",
                        "answerId": "",
                        "questionId": existing_qid if preserve_id else "",
                        "type": 0,
                        "extend": {"pic_url": []},
                    })
                    # 额外答案
                    if extra_answer:
                        answer_arr.append({
                            "answerContent": "",
                            "answerId": "",
                            "questionId": existing_qid if preserve_id else "",
                            "type": 1,
                            "extend": {
                                "pic_url": [],
                                "extra_required": 1 if extra_answer.get("required", False) else 0,
                                "extra_label": extra_answer.get("label", "其他"),
                            },
                        })

                    question_info: dict[str, Any] = {
                        "questionId": existing_qid if preserve_id else "",
                        "questionTitle": title,
                        "sessionId": session_id if preserve_id else "",
                        "pattern": "0",
                        "level": "2",
                        "domType": "radio",
                        "questionIndex": str(idx + 1),
                        "showIndex": idx,
                        "multimedia_id": "0",
                        "desc": "",
                        "show_type": [],
                        "setup": {
                            "required": "1" if required else "0",
                            "screenOrderType": screen_order_type,
                        },
                        "multimedia_type": 0,
                        "extend": {"pic_url": []},
                        "cid": _next_cid(),
                        "knowledge_point": [],
                        "hasExtraAnswer": has_extra,
                    }
                    section_elements.append((idx, {"questionInfo": question_info, "answerArr": answer_arr}))

                elif q_type == "checkbox":
                    options = q.get("options", [])
                    if not options:
                        raise ValueError(f"第 {idx + 1} 题（多选）必须提供 options")

                    extra_answer = q.get("extra_answer")
                    has_extra = 1 if extra_answer else 0
                    min_options = q.get("min_options", 0)
                    max_options = q.get("max_options", 0)

                    answer_arr = []
                    for opt_idx, opt in enumerate(options):
                        old_answer = existing_answers[opt_idx] if opt_idx < len(existing_answers) else {}
                        answer_arr.append({
                            "answerId": str(old_answer.get("answerId", "")) if old_answer else "",
                            "answerIdx": opt_idx + 1,
                            "questionId": existing_qid if preserve_id else "",
                            "answerContent": str(opt),
                            "isRight": "0",
                            "extend": {"pic_url": []},
                            "type": 0,
                            "isFocus": False,
                        })
                    answer_arr.append({
                        "answerContent": "",
                        "answerId": "",
                        "questionId": existing_qid if preserve_id else "",
                        "type": 0,
                        "extend": {"pic_url": []},
                    })
                    if extra_answer:
                        answer_arr.append({
                            "answerContent": "",
                            "answerId": "",
                            "questionId": existing_qid if preserve_id else "",
                            "type": 1,
                            "extend": {
                                "pic_url": [],
                                "extra_required": 1 if extra_answer.get("required", False) else 0,
                                "extra_label": extra_answer.get("label", "其他"),
                            },
                        })

                    setup: dict[str, Any] = {
                        "required": "1" if required else "0",
                        "screenOrderType": screen_order_type,
                    }
                    if min_options > 0:
                        setup["limitOptionsMin"] = min_options
                    if max_options > 0:
                        setup["limitOptionsMax"] = max_options

                    question_info = {
                        "questionId": existing_qid if preserve_id else "",
                        "questionTitle": title,
                        "sessionId": session_id if preserve_id else "",
                        "pattern": "0",
                        "level": "2",
                        "domType": "checkbox",
                        "questionIndex": str(idx + 1),
                        "showIndex": idx,
                        "multimedia_id": "0",
                        "desc": "",
                        "show_type": [],
                        "setup": setup,
                        "multimedia_type": 0,
                        "extend": {"pic_url": []},
                        "cid": _next_cid(),
                        "knowledge_point": [],
                        "hasExtraAnswer": has_extra,
                    }
                    section_elements.append((idx, {"questionInfo": question_info, "answerArr": answer_arr}))

                elif q_type == "textarea":
                    default_answer = q.get("default_answer", "")

                    old_answer_id = ""
                    if preserve_id and existing_answers:
                        old_answer_id = str(existing_answers[0].get("answerId", ""))

                    question_info = {
                        "questionId": existing_qid if preserve_id else "",
                        "sessionId": session_id if preserve_id else "",
                        "questionTitle": title,
                        "questionIndex": str(idx + 1),
                        "pattern": "0",
                        "level": "2",
                        "domType": "textarea",
                        "showIndex": idx,
                        "multimedia_id": "0",
                        "setup": {
                            "required": "1" if required else "0",
                            "screenOrderType": screen_order_type,
                        },
                        "multimedia_type": 0,
                        "extend": {"pic_url": []},
                        "cid": _next_cid(),
                        "knowledge_point": [],
                        "hasExtraAnswer": 0,
                    }
                    answer_arr = [{
                        "answerContent": default_answer,
                        "answerId": old_answer_id,
                        "questionId": existing_qid if preserve_id else "",
                    }]
                    section_elements.append((idx, {"questionInfo": question_info, "answerArr": answer_arr}))

                elif q_type == "number":
                    min_value = q.get("min_value", 1)
                    max_value = q.get("max_value", 5)
                    min_label = q.get("min_label", "")
                    max_label = q.get("max_label", "")
                    default_value = q.get("default_value")

                    setup = {
                        "required": "1" if required else "0",
                        "screenOrderType": screen_order_type,
                    }
                    if default_value is not None:
                        setup["defaultValue"] = default_value

                    extend: dict[str, Any] = {
                        "pic_url": [],
                        "min": min_value,
                        "max": max_value,
                    }
                    if min_label:
                        extend["minDesc"] = min_label
                    if max_label:
                        extend["maxDesc"] = max_label

                    question_info = {
                        "questionId": existing_qid if preserve_id else "",
                        "sessionId": session_id if preserve_id else "",
                        "questionTitle": title,
                        "questionIndex": str(idx + 1),
                        "pattern": "0",
                        "level": "2",
                        "domType": "number",
                        "showIndex": idx,
                        "multimedia_id": "0",
                        "setup": setup,
                        "multimedia_type": 0,
                        "extend": extend,
                        "cid": _next_cid(),
                        "knowledge_point": [],
                        "hasExtraAnswer": 0,
                    }
                    section_elements.append((idx, {"questionInfo": question_info, "answerArr": []}))

                elif q_type == "paragraph":
                    content = q.get("content", "")
                    if not content:
                        raise ValueError(f"第 {idx + 1} 项（段落说明）必须提供 content")

                    # 段落说明：保留原有 multimedia_id 和 questionId
                    multimedia_id = ""
                    if preserve_id:
                        multimedia_id = str(existing_qinfo.get("multimedia_id", ""))

                    if not multimedia_id:
                        # 新增段落说明：创建富文本
                        multimedia_id = self._create_fulltext(
                            content=content,
                            ref_type="question",
                        )

                    question_info = {
                        "questionId": existing_qid if preserve_id else "",
                        "sessionId": session_id if preserve_id else "",
                        "questionTitle": "",
                        "questionIndex": "0",
                        "pattern": "4",
                        "level": "2",
                        "domType": "paragraph",
                        "showIndex": idx,
                        "multimedia_id": str(multimedia_id),
                        "desc": content,
                        "show_type": [],
                        "setup": {},
                        "multimedia_type": 0,
                        "extend": {},
                        "cid": _next_cid() if not preserve_id else str(existing_qinfo.get("cid", _next_cid())),
                        "knowledge_point": [],
                        "multimedia_weight": idx + 1,
                    }
                    section_elements.append((idx, {"questionInfo": question_info, "answerArr": []}))

                    # 仅在保留旧段落时添加到 multimediaArr（新增段落无 questionId）
                    if preserve_id and existing_qid:
                        multimedia_arr.append({
                            "multimedia_id": str(multimedia_id),
                            "ref_type": "question",
                            "ref_id": existing_qid,
                        })

                else:
                    raise ValueError(
                        f"第 {idx + 1} 题: 不支持的题目类型 '{q_type}'。"
                        f"支持: radio, checkbox, textarea, number, paragraph"
                    )

            # 处理剩余的旧题目（删除）
            for idx in range(len(questions), len(existing_sections)):
                existing_section = existing_sections[idx]
                existing_qinfo = existing_section.get("questionInfo", {})
                existing_qid = str(existing_qinfo.get("questionId", ""))
                if existing_qid:
                    deleted_question_ids.append(existing_qid)

            # 按 showIndex 排序并生成最终 sectionArr
            section_elements.sort(key=lambda x: x[0])
            new_section_arr = [sec for _, sec in section_elements]

        # 4. 处理 sessionInfo 设置变更
        if questions is None:
            # 不修改题目时，保留原有 sectionArr
            new_section_arr = existing_sections

        # 辅助函数：将日期时间字符串转为 Unix 时间戳（秒）
        def _to_timestamp(value: int | str) -> int:
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                # 尝试解析常见格式：2026-06-08 09:30 或 2026/06/08 09:30
                for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
                    try:
                        dt = datetime.strptime(value.strip(), fmt)
                        return int(dt.replace(tzinfo=timezone.utc).timestamp())
                    except ValueError:
                        continue
                # 尝试直接转为 int（已经是时间戳字符串）
                try:
                    return int(value)
                except ValueError:
                    raise ValueError(f"无法解析时间格式: {value!r}，支持格式: YYYY-MM-DD HH:MM")
            return 0

        # 更新设置字段
        setup = updated_session_info.get("setup", {})
        if not isinstance(setup, dict):
            setup = {}
            updated_session_info["setup"] = setup

        if jump_button is not None:
            setup["jumpButton"] = "1" if jump_button else "0"
            changes["jump_button"] = jump_button
        if show_user_result is not None:
            setup["showUserResult"] = "1" if show_user_result else "0"
            changes["show_user_result"] = show_user_result
        if is_show_participate_on_screen is not None:
            setup["isShowParticipateOnScreen"] = "1" if is_show_participate_on_screen else "0"
            changes["is_show_participate_on_screen"] = is_show_participate_on_screen
        if share_status is not None:
            # HAR 证据：shareStatus 为字符串 "1"/"2"/"3"
            setup["shareStatus"] = str(share_status)
            changes["share_status"] = share_status
        if submit_permission is not None:
            setup["submitPermission"] = str(submit_permission)
            changes["submit_permission"] = submit_permission
        if allow_modify is not None:
            setup["allow_modify"] = 1 if allow_modify else 0
            changes["allow_modify"] = allow_modify
        if submit_limit is not None:
            # HAR 证据：submit_limit 为整数
            setup["submit_limit"] = int(submit_limit) if str(submit_limit).isdigit() else submit_limit
            changes["submit_limit"] = submit_limit
        if result_prompt is not None:
            # 必须使用 camelCase，后端不接受 snake_case 的 result_prompt
            setup["resultPrompt"] = result_prompt
            changes["result_prompt"] = result_prompt
        if accept_submission_time is not None:
            setup["accept_submission_time"] = _to_timestamp(accept_submission_time)
            changes["accept_submission_time"] = accept_submission_time
        if refuse_submission_time is not None:
            setup["refuse_submission_time"] = _to_timestamp(refuse_submission_time)
            changes["refuse_submission_time"] = refuse_submission_time
        if random_option is not None:
            # 必须使用 camelCase，后端不接受 snake_case 的 random_option
            setup["randomOption"] = "1" if random_option else "0"
            changes["random_option"] = random_option
        if jump_url is not None:
            # 必须使用 camelCase，后端不接受 snake_case 的 jump_url
            setup["jumpUrl"] = jump_url
            changes["jump_url"] = jump_url
        if jump_button_title is not None:
            # 必须使用 camelCase，后端不接受 snake_case 的 jump_button_title
            setup["jumpButtonTitle"] = jump_button_title
            changes["jump_button_title"] = jump_button_title
        if type_name is not None:
            setup["type_name"] = type_name
            changes["type_name"] = type_name

        if session_title is not None:
            updated_session_info["sessionTitle"] = session_title
            changes["session_title"] = session_title
        if desc is not None:
            updated_session_info["desc"] = desc
            changes["desc"] = desc
            # 关键发现：当小节通过富文本系统存储 desc 时（multimedia_id != 0），
            # 直接设置 desc 不生效。必须将 multimedia_id 设为 0 才能使用 desc 字段。
            current_mm_id = str(updated_session_info.get("multimedia_id", "0"))
            if current_mm_id and current_mm_id != "0":
                updated_session_info["multimedia_id"] = 0
                changes["multimedia_id_cleared"] = current_mm_id
        if is_required is not None:
            updated_session_info["is_require"] = 1 if is_required else 0
            changes["is_required"] = is_required
        if tags is not None:
            updated_session_info["tags"] = [{"tag": str(tag)} for tag in tags]
            changes["tags"] = tags

        # 5. 构造 session_data
        # 判断是否有 sessionInfo 级别的变更（标题/描述/设置/必修/标签）
        session_info_change_params = [
            session_title, desc, is_required, jump_button, jump_url,
            jump_button_title, show_user_result, is_show_participate_on_screen,
            share_status, submit_permission, allow_modify, submit_limit,
            result_prompt, accept_submission_time, refuse_submission_time,
            random_option, type_name, tags,
        ]
        has_session_info_changes = any(p is not None for p in session_info_change_params)

        if has_session_info_changes or questions is None:
            # 需要提交完整 sessionInfo（包含设置变更）
            session_data: dict[str, Any] = {
                "sessionInfo": updated_session_info,
                "sectionArr": new_section_arr,
            }
        else:
            # 仅修改题目/顺序：使用简化 sessionInfo（与前端编辑行为一致）
            session_data = {
                "sessionInfo": {
                    "multimedia_type": updated_session_info.get("multimedia_type", "1"),
                    "multimedia_id": updated_session_info.get("multimedia_id", 0),
                },
                "sectionArr": new_section_arr,
            }

        # 仅在编辑题目且有段落说明时添加 multimediaArr
        if questions is not None and multimedia_arr:
            session_data["multimediaArr"] = multimedia_arr

        # 6. 调用 savesession
        # force_edit=1 必须放在 session_data JSON 顶层，不是 form 字段
        # （HAR 逆向工程发现，这是触发编辑模式的关键；但题目真正删除必须
        #  走单独的 /ajax/e_deleteQuestion，savesession 中的最小化条目不会
        #  触发删除）
        session_data["force_edit"] = 1
        resp = self.client.post(
            self.client.desktop_url("/api/session/savesession"),
            data={
                "group_id": group_id,
                "session_id": session_id,
                "session_data": json.dumps(session_data, ensure_ascii=False),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error(
                "savesession 编辑错误响应: %s",
                json.dumps(resp, ensure_ascii=False),
            )
            raise RuntimeError(
                f"更新问卷小节失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        # 7. 删除被标记的题目（savesession 不处理删除，必须单独调用）
        if questions is not None and deleted_question_ids:
            for dqid in deleted_question_ids:
                try:
                    del_resp = self.client.post(
                        self.client.desktop_url("/ajax/e_deleteQuestion"),
                        data={
                            "sessionId": session_id,
                            "questionId": dqid,
                            "force_edit": "1",
                        },
                    )
                    if del_resp.get("status") not in (True, "true"):
                        logger.warning(
                            "删除题目失败: qid=%s, resp=%s",
                            dqid,
                            json.dumps(del_resp, ensure_ascii=False),
                        )
                except Exception as e:
                    logger.warning("删除题目异常: qid=%s, err=%s", dqid, e)
            changes["deleted_questions"] = deleted_question_ids

        logger.info(
            "问卷小节更新成功: session_id=%s, changes=%s, deleted=%s",
            session_id,
            list(changes.keys()),
            deleted_question_ids,
        )

        # 8. 保存标签（非致命）
        if tags is not None:
            try:
                self._save_keywords(session_id, tags=tags)
            except Exception as e:
                logger.warning("标签保存失败（非致命）: %s", e)

        return {
            "session_id": session_id,
            "group_id": group_id,
            "changes": list(changes.keys()),
            "deleted_question_ids": deleted_question_ids,
        }

    def update_infographic_section(
        self,
        group_id: str,
        session_id: str,
        session_title: str | None = None,
        content_blocks: list[dict[str, str]] | None = None,
        cover_image_path: str | None = None,
        cover_resource_id: str | None = None,
        remove_cover: bool = False,
        is_required: bool | None = None,
        type_name: str | None = None,
        min_duration_seconds: int | None = None,
        max_duration_seconds: int | None = None,
        show_course_creator_info: bool | None = None,
        show_article_reading_speed: bool | None = None,
        is_comment_time_visible: bool | None = None,
        enable_comment: bool | None = None,
        tags: list[str] | None = None,
        resource_imgText_id: str | None = None,
    ) -> dict[str, Any]:
        """修改已有图文小节的属性和内容.

        基于 HAR 分析：修改图文小节流程：
        1. getsessionbaseinfo 获取现有数据
        2. savesession 修改设置
        3. imgtextupd 修改图文内容（含 resource_imgText_id）
        4. bind-upd(v1) 更新资源绑定

        Args:
            group_id: 课程 ID
            session_id: 小节 ID
            session_title: 新标题（None=不修改）
            content_blocks: 新图文内容列表（None=不修改）
            cover_image_path: 新封面图本地路径（None=不修改）
            cover_resource_id: 已上传的封面图资源 ID（None=不修改）
            remove_cover: 是否移除封面图（默认 False）
            is_required: 是否必修（None=不修改）
            type_name: 小节类型标签（None=不修改）
            min_duration_seconds: 最小学习时长（None=不修改）
            max_duration_seconds: 学习时长统计上限（None=不修改）
            show_course_creator_info: 展示课程创建者信息（None=不修改）
            show_article_reading_speed: 展示阅读速度（None=不修改）
            is_comment_time_visible: 允许查看发言时间（None=不修改）
            enable_comment: 开启发言区（None=不修改）
            tags: 新标签列表（None=不修改，[]=清空标签）

        Returns:
            包含修改字段列表和更新后信息的字典

        Raises:
            RuntimeError: 获取或保存失败
            ValueError: 参数冲突
        """
        # 参数校验
        if cover_image_path is not None and cover_resource_id is not None:
            raise ValueError(
                "cover_image_path 和 cover_resource_id 不能同时提供，请二选一"
            )
        if remove_cover and (cover_image_path or cover_resource_id):
            raise ValueError(
                "remove_cover 与 cover_image_path/cover_resource_id 不能同时使用"
            )

        # 1. 获取现有小节数据
        logger.info("获取图文小节现有数据: session_id=%s", session_id)
        resp = self.client.get(
            self.client.desktop_url("/api/session/getsessionbaseinfo"),
            params={"session_id": session_id},
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(
                f"获取小节数据失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        raw_data = resp.get("data", {})
        if not raw_data:
            raise RuntimeError("getsessionbaseinfo 返回数据为空")

        session_info = dict(raw_data)
        old_setup = dict(session_info.get("setup", {}))
        modified_fields: list[str] = []

        # 2. 过滤只读字段
        for field in _READONLY_SESSIONINFO_FIELDS:
            session_info.pop(field, None)

        # 3. 应用用户变更
        if session_title is not None:
            session_info["sessionTitle"] = session_title
            modified_fields.append("session_title")

        if is_required is not None:
            session_info["is_require"] = 1 if is_required else 0
            modified_fields.append("is_required")

        if tags is not None:
            session_info["tags"] = [{"tag": str(tag)} for tag in tags]
            modified_fields.append("tags")

        # 4. 处理 setup 变更
        setup = dict(old_setup)
        for field in self._SETUP_READONLY_FIELDS:
            setup.pop(field, None)

        if type_name is not None:
            if type_name:
                setup["type_name"] = type_name
            else:
                setup.pop("type_name", None)
            modified_fields.append("type_name")

        if min_duration_seconds is not None:
            setup["vlt_min"] = min_duration_seconds
            modified_fields.append("min_duration_seconds")

        if max_duration_seconds is not None:
            setup["vlt_max"] = max_duration_seconds
            modified_fields.append("max_duration_seconds")

        if show_course_creator_info is not None:
            setup["show_course_creator_info"] = (
                "1" if show_course_creator_info else "0"
            )
            modified_fields.append("show_course_creator_info")

        if show_article_reading_speed is not None:
            setup["show_article_reading_speed"] = (
                "1" if show_article_reading_speed else "0"
            )
            modified_fields.append("show_article_reading_speed")

        if is_comment_time_visible is not None:
            setup["is_comment_time_visible"] = (
                "1" if is_comment_time_visible else "0"
            )
            modified_fields.append("is_comment_time_visible")

        if enable_comment is not None:
            setup["close_comment_switch"] = 0 if enable_comment else 1
            modified_fields.append("enable_comment")

        session_info["setup"] = setup

        # 5. 处理封面图变更
        new_cover_resource_id = ""
        if remove_cover:
            new_cover_resource_id = ""
            modified_fields.append("remove_cover")
        elif cover_image_path is not None:
            new_cover_resource_id = self._upload_cover_image(
                cover_image_path=cover_image_path,
                fatal=True,
            )
            modified_fields.append("cover_image")
        elif cover_resource_id is not None:
            new_cover_resource_id = cover_resource_id
            modified_fields.append("cover_resource_id")

        # 6. 构造 session_data
        session_data = {
            "sessionInfo": session_info,
            "sectionArr": [],
        }

        # 7. 调用 savesession
        logger.info(
            "保存图文小节修改: session_id=%s, modified=%s",
            session_id, modified_fields,
        )
        resp = self.client.post(
            self.client.desktop_url("/api/session/savesession"),
            data={
                "group_id": group_id,
                "session_id": session_id,
                "session_data": json.dumps(
                    session_data, ensure_ascii=False
                ),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error(
                "savesession 更新错误: %s",
                json.dumps(resp, ensure_ascii=False),
            )
            raise RuntimeError(
                f"更新图文小节失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        logger.info("图文小节设置修改成功: session_id=%s", session_id)

        # 8. 处理图文内容变更
        if content_blocks is not None:
            # 上传内容中的本地图片
            processed_blocks = self._upload_infographic_content_images(
                content_blocks
            )

            # 获取现有内容（用于保留 id）
            # 优先使用传入的 resource_imgText_id，其次从 extend 获取
            _img_text_id = resource_imgText_id or str(
                raw_data.get("extend", {}).get("resource_imgText_id", "")
            )
            if not _img_text_id:
                raise RuntimeError(
                    "修改图文内容需要提供 resource_imgText_id。"
                    "该字段在 getsessionbaseinfo 中不返回，"
                    "需从创建时保存或先调用 getimgtextlist 获取。"
                )
            existing_items: list[dict[str, Any]] = []
            if _img_text_id:
                try:
                    existing_items = self._get_imgtextlist(_img_text_id)
                except Exception as e:
                    logger.warning("获取现有图文内容失败: %s", e)

            try:
                self._call_imgtextupd(
                    session_id=session_id,
                    content_blocks=processed_blocks,
                    resource_imgText_id=_img_text_id,
                    existing_items=existing_items,
                )
                modified_fields.append("content_blocks")
            except Exception as e:
                logger.error("图文内容修改失败: %s", e)
                raise RuntimeError(
                    f"图文小节设置已更新但内容修改失败: {e}"
                )

        # 9. 更新资源绑定（封面）
        if new_cover_resource_id:
            self._bind_infographic_resources_v1(
                session_id=session_id,
                cover_resource_id=new_cover_resource_id,
            )

        # 10. 保存标签
        self._save_keywords(session_id, tags=tags)

        return {
            "session_id": session_id,
            "group_id": group_id,
            "modified_fields": modified_fields,
            "title": session_title or session_info.get("sessionTitle", ""),
            "cover_resource_id": new_cover_resource_id or None,
        }

    # ------------------------------------------------------------------
    # 切换小节可见性（关闭/打开）
    # ------------------------------------------------------------------

    def toggle_session_visibility(
        self,
        session_id: str,
        visible: bool,
    ) -> dict[str, Any]:
        """切换小节对学员的可见性（关闭/打开）.

        关闭后学员无法看到该小节，也无法学习。
        打开后恢复学员的学习和查看能力。

        Args:
            session_id: 小节 ID
            visible: True=打开（可见），False=关闭（不可见）

        Returns:
            包含操作结果的字典

        Raises:
            RuntimeError: 操作失败
        """
        access_permission = 1 if visible else 0
        status_text = "打开" if visible else "关闭"

        resp = self.client.post(
            self.client.desktop_url("/api/group/setsessionpermission"),
            data={
                "session_id": session_id,
                "access_permission": str(access_permission),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(
                f"{status_text}小节失败: {resp.get('error', resp.get('error_message', 'unknown'))}"
            )

        logger.info("小节已%s: session_id=%s", status_text, session_id)

        return {
            "session_id": session_id,
            "visible": visible,
            "action": status_text,
        }

    # ------------------------------------------------------------------
    # 更新资源绑定（支持 unbind）
    # ------------------------------------------------------------------

    def _update_resource_binding(
        self,
        session_id: str,
        new_resource_id: str,
        old_resource_id: str,
        cover_resource_id: str = "",
    ) -> None:
        """更新资源绑定关系（含 unbind 旧资源）.

        Args:
            session_id: 小节 ID
            new_resource_id: 新资源 ID
            old_resource_id: 旧资源 ID（用于 unbind）
            cover_resource_id: 封面图资源 ID

        Raises:
            RuntimeError: 绑定更新失败
        """
        resource_data: list[dict[str, Any]] = [
            {
                "resource_type": 1,  # 1=视频/SCORM 资源
                "bind_resource_ids": [new_resource_id],
                "unbind_resource_ids": [old_resource_id],
            }
        ]

        if cover_resource_id:
            resource_data.append({
                "resource_type": 6,  # 6=图片资源
                "bind_resource_ids": [cover_resource_id],
                "unbind_resource_ids": [],
            })

        resp = self.client.post(
            self.client.desktop_url("/uapi/v2/resource/bind-upd"),
            data={
                "parent_id": session_id,
                "parent_type": "4",  # 4=session 类型
                "resource_data": json.dumps(resource_data, ensure_ascii=False),
            },
        )

        if resp.get("error_code") != 0:
            raise RuntimeError(
                f"资源绑定更新失败: {resp.get('error_message', 'unknown')}"
            )

        logger.info(
            "资源绑定更新成功: session_id=%s, new=%s, old=%s",
            session_id,
            new_resource_id,
            old_resource_id,
        )

    # ------------------------------------------------------------------
    # 创建签到小节
    # ------------------------------------------------------------------

    def create_signin_section(
        self,
        group_id: str,
        session_title: str,
        signin_info_list: list[dict[str, Any]],
        auto_check: bool = True,
        is_required: bool = True,
        point_ratio: int = 1,
        is_anti_fraud: bool = False,
        mini_program_switch: bool = True,
        share_status: int = 1,
        result_prompt: str = "",
        type_name: str = "",
        desc_richtext: str = "",
        tags: list[str] | None = None,
        sort_order: int = 0,
    ) -> dict[str, Any]:
        """在课程中创建签到类型小节.

        签到小节使用 sessionType="6"，调用 /api/session/savesession。
        签到信息（学员签到时需要回答的问题）通过 signin_info_list 传入。

        签到信息格式（signin_info_list 列表中每项为 dict）：

        **文本输入 (type="textarea")：**
        {
            "type": "textarea",
            "title": "您的姓名是？",
            "required": true,
            "hint": "提示文字（可选，作为占位提示显示）"
        }

        **单选题 (type="radio")：**
        {
            "type": "radio",
            "title": "您的性别是？",
            "required": true,
            "options": ["女", "男"]
        }

        **多选题 (type="checkbox")：**
        {
            "type": "checkbox",
            "title": "谁是你的朋友",
            "required": false,
            "options": ["黄飞鸿", "洪七公", "周伯通"],
            "min_options": 1,  // 最少选几项（可选，默认1）
            "max_options": 2   // 最多选几项（可选，默认等于选项数）
        }

        **数值题 (type="number")：**
        {
            "type": "number",
            "title": "您的工作年限是？",
            "required": false,
            "min": 0,      // 最小值（可选，默认0）
            "max": 50,     // 最大值（可选，默认100）
            "default": 0   // 默认值（可选，默认等于min）
        }

        **段落说明 (type="paragraph")：**
        {
            "type": "paragraph",
            "content": "<p>这是一个段落说明</p>"  // 支持 HTML
        }

        签到设置参数：
        - auto_check: True=自动审核(默认), False=手动审核
        - is_anti_fraud: True=开启防作弊, False=关闭(默认)
        - mini_program_switch: True=开启小程序(默认), False=关闭
        - share_status: 1=课程内公开(默认)
        - result_prompt: 签到成功提示语（如"签到成功！"）

        Args:
            group_id: 课程 ID
            session_title: 签到小节标题
            signin_info_list: 签到信息（问题）列表
            auto_check: 是否自动审核签到（默认 True）
            is_required: 是否必修（默认 True）
            point_ratio: 积分倍率（默认 1）
            is_anti_fraud: 是否开启防作弊（默认 False）
            mini_program_switch: 是否开启小程序（默认 True）
            share_status: 分享状态（默认 1=课程内公开）
            result_prompt: 签到成功提示语
            type_name: 小节类型标签（如"签到"）
            desc_richtext: 富文本签到说明（HTML）
            tags: 标签文本列表
            sort_order: 排序序号，0 表示自动追加

        Returns:
            包含 session_id、group_id、title、signin_info_count 等信息的字典

        Raises:
            RuntimeError: 创建小节失败
            ValueError: 参数不合法
        """
        if not signin_info_list:
            raise ValueError("signin_info_list 不能为空，签到小节至少需要包含一个签到信息")

        # 1. 创建富文本说明（如有）
        multimedia_id = ""
        if desc_richtext:
            try:
                multimedia_id = self._create_fulltext(desc_richtext)
                logger.info("签到说明富文本创建成功: multimedia_id=%s", multimedia_id)
            except Exception as e:
                logger.warning("签到说明富文本创建失败（非致命）: %s", e)

        # 2. 构建 sectionArr（签到信息数组）
        cid_counter = 0

        def _next_cid() -> str:
            nonlocal cid_counter
            cid = f"c_{int(time.time() * 1000)}_{cid_counter}"
            cid_counter += 1
            return cid

        section_arr: list[dict[str, Any]] = []

        for idx, info in enumerate(signin_info_list):
            info_type = info.get("type", "").lower()
            title = info.get("title", "")
            required = bool(info.get("required", False))

            if info_type == "textarea":
                # 文本输入题
                hint = info.get("hint", "")
                answer_arr = [{"answerContent": hint}] if hint else [{"answerContent": ""}]

                question_info = {
                    "extend": {"pic_url": []},
                    "setup": {"required": "0" if required else "1"},
                    "questionId": "",
                    "questionTitle": title,
                    "mobileQuestionTitle": title,
                    "pattern": "3",
                    "required": False,
                    "domType": "textarea",
                    "templateId": "",
                    "questionIndex": str(idx + 1),
                    "cid": _next_cid(),
                }
                section_arr.append({"questionInfo": question_info, "answerArr": answer_arr})

            elif info_type == "radio":
                # 单选题
                options = info.get("options", [])
                if not options:
                    raise ValueError(f"第 {idx + 1} 题（单选）必须提供 options")

                answer_arr = []
                for opt in options:
                    answer_arr.append({
                        "answerContent": str(opt),
                        "extend": {"pic_url": []},
                    })

                question_info = {
                    "extend": {"pic_url": []},
                    "setup": {"required": "0" if required else "1"},
                    "questionId": "",
                    "questionTitle": title,
                    "mobileQuestionTitle": title,
                    "pattern": "0",
                    "required": False,
                    "domType": "radio",
                    "templateId": "",
                    "questionIndex": str(idx + 1),
                    "cid": _next_cid(),
                }
                section_arr.append({"questionInfo": question_info, "answerArr": answer_arr})

            elif info_type == "checkbox":
                # 多选题
                options = info.get("options", [])
                if not options:
                    raise ValueError(f"第 {idx + 1} 题（多选）必须提供 options")

                min_opts = info.get("min_options", 0)
                max_opts = info.get("max_options", 0)

                answer_arr = []
                for opt in options:
                    answer_arr.append({
                        "answerContent": str(opt),
                        "extend": {"pic_url": []},
                        "isFocus": False,
                    })
                # 前端惯例：多选题最后一个选项后加一个空选项
                answer_arr.append({
                    "answerContent": "",
                    "answerId": "",
                    "questionId": "",
                    "type": 0,
                    "extend": {"pic_url": []},
                })

                question_setup = {"required": "0" if required else "1"}
                if min_opts > 0:
                    question_setup["limitOptionsMin"] = min_opts
                if max_opts > 0:
                    question_setup["limitOptionsMax"] = max_opts

                question_info = {
                    "extend": {"pic_url": []},
                    "setup": question_setup,
                    "questionId": "",
                    "questionTitle": title,
                    "mobileQuestionTitle": title,
                    "pattern": "1",
                    "required": False,
                    "domType": "checkbox",
                    "templateId": "",
                    "questionIndex": str(idx + 1),
                    "cid": _next_cid(),
                }
                section_arr.append({"questionInfo": question_info, "answerArr": answer_arr})

            elif info_type == "number":
                # 数值题（滑块/数值输入）
                min_value = int(info.get("min", 0))
                max_value = int(info.get("max", 100))
                default_value = int(info.get("default", min_value))

                question_info = {
                    "extend": {"pic_url": [], "min": min_value, "max": max_value},
                    "setup": {
                        "required": "0" if required else "1",
                        "defaultValue": default_value,
                    },
                    "questionId": "",
                    "questionTitle": title,
                    "mobileQuestionTitle": title,
                    "pattern": "8",
                    "required": False,
                    "domType": "number",
                    "templateId": "",
                    "questionIndex": str(idx + 1),
                    "cid": _next_cid(),
                }
                section_arr.append({"questionInfo": question_info, "answerArr": []})

            elif info_type == "paragraph":
                # 段落说明（无答案）
                content = info.get("content", "")
                question_info = {
                    "questionId": "",
                    "sessionId": "",
                    "questionTitle": "",
                    "questionIndex": "",
                    "pattern": "4",
                    "required": "",
                    "creatTime": "",
                    "creatTimeShow": "",
                    "domType": "paragraph",
                    "totalCount": "",
                    "showType": {},
                    "showIndex": 0,
                    "setup": {},
                    "extend": {},
                    "cid": _next_cid(),
                    "desc": content,
                }
                section_arr.append({"questionInfo": question_info, "answerArr": []})

            else:
                raise ValueError(f"不支持的签到信息类型: {info_type}（第 {idx + 1} 题）")

        # 3. 构造 sessionInfo
        # 只要有签到问题就开启高级模式（advance=1），与前端编辑器行为一致。
        setup: dict[str, Any] = {
            "advance": 1,
            "basicQuestionCount": 1,
            "is_anti_fraud": "1" if is_anti_fraud else "0",
            "mini_program_switch": "1" if mini_program_switch else "0",
            "shareStatus": str(share_status),
            "type_name": type_name or "签到",
            "allow_drag_track": "1",
            "allow_adjust_speed": 1,
            "is_allow_download": 0,
            "isAllowDownload": 0,
        }
        if result_prompt:
            setup["resultPrompt"] = result_prompt

        session_info: dict[str, Any] = {
            "autoCheck": 1 if auto_check else 0,
            "creatTime": "",
            "creatTimeShow": "",
            "groupId": "",
            "onlineUserCount": "",
            "resultType": "",
            "sessionId": "",
            "sessionInUse": False,
            "sessionIndex": sort_order,
            "sessionStatus": "",
            "sessionTitle": session_title,
            "sessionType": "6",
            "teacherId": "",
            "desc": "",
            "totalCount": "",
            "studentRegFlag": False,
            "totalUserCount": "",
            "multimedia_type": 1,
            "multimedia_id": multimedia_id or 0,
            "setup": setup,
            "extend": {},
            "point_ratio": point_ratio,
            "is_require": 1 if is_required else 0,
        }

        # 4. 构造 session_data
        session_data = {
            "sessionInfo": session_info,
            "sectionArr": section_arr,
        }

        # 5. 调用 savesession
        resp = self.client.post(
            self.client.desktop_url("/api/session/savesession"),
            data={
                "group_id": group_id,
                "session_data": json.dumps(session_data, ensure_ascii=False),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error("创建签到小节失败: %s", resp.get("error", "unknown"))
            raise RuntimeError(f"创建签到小节失败: {resp.get('error', 'unknown')}")

        data = resp.get("data", {})
        result_session_id = str(data.get("session_id", ""))

        logger.info(
            "签到小节创建成功: session_id=%s, group_id=%s, title=%s, info_count=%d",
            result_session_id, group_id, session_title, len(signin_info_list),
        )

        return {
            "session_id": result_session_id,
            "group_id": group_id,
            "title": session_title,
            "signin_info_count": len(signin_info_list),
            "multimedia_id": multimedia_id,
        }

    # ------------------------------------------------------------------
    # 更新签到小节
    # ------------------------------------------------------------------

    def update_signin_section(
        self,
        group_id: str,
        session_id: str,
        session_title: str | None = None,
        signin_info_list: list[dict[str, Any]] | None = None,
        auto_check: bool | None = None,
        is_required: bool | None = None,
        point_ratio: int | None = None,
        is_anti_fraud: bool | None = None,
        mini_program_switch: bool | None = None,
        share_status: int | None = None,
        result_prompt: str | None = None,
        type_name: str | None = None,
        desc_richtext: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """更新课程中已有的签到类型小节.

        采用"先获取完整数据，再应用变更"的模式：
        1. 调用 getsessionInfo 获取现有小节最新完整数据
        2. 过滤只读字段
        3. 应用用户传入的变更（只改提供的字段，None 表示不修改）
        4. 调用 savesession 提交完整数据（必须包含 session_id）

        签到信息匹配规则：
        - signin_info_list 按索引位置与现有 sectionArr 匹配
        - 相同位置且 type 相同 → 保留原有 questionId/answerId，更新其他字段
        - type 不同 → 旧信息删除，新信息新增
        - 新数组长度超过旧数组 → 超出部分为新增
        - 新数组长度短于旧数组 → 缺少部分保留原信息

        Args:
            group_id: 课程 ID
            session_id: 小节 ID
            session_title: 新小节标题，None 表示不修改
            signin_info_list: 新的签到信息列表（完整列表，按目标顺序排列），None 表示不修改
            auto_check: 是否自动审核，None 表示不修改
            is_required: 是否必修，None 表示不修改
            point_ratio: 积分倍率，None 表示不修改
            is_anti_fraud: 是否开启防作弊，None 表示不修改
            mini_program_switch: 是否开启小程序，None 表示不修改
            share_status: 分享状态，None 表示不修改
            result_prompt: 签到成功提示语，None 表示不修改
            type_name: 小节类型标签，None 表示不修改
            desc_richtext: 富文本签到说明（HTML），None 表示不修改，空字符串表示清除
            tags: 标签列表，None 表示不修改

        Returns:
            包含 session_id 和 changes 列表的字典

        Raises:
            RuntimeError: 更新失败
        """
        # 1. 获取现有小节最新完整数据
        existing = self._get_session_detail(session_id)
        session_info = existing.get("sessionInfo", {})
        existing_sections = existing.get("sectionArr", [])

        # 2. 深拷贝并过滤只读字段
        updated_info = copy.deepcopy(session_info)
        for ro_field in _READONLY_SESSIONINFO_FIELDS:
            updated_info.pop(ro_field, None)

        changes: list[str] = []
        setup = updated_info.get("setup", {})

        # 3. 处理富文本说明变更
        current_multimedia_id = str(updated_info.get("multimedia_id", "") or "")
        if desc_richtext is not None:
            if desc_richtext:
                # 创建或更新富文本
                if current_multimedia_id and current_multimedia_id != "0":
                    try:
                        self._update_fulltext(current_multimedia_id, desc_richtext, group_id)
                        changes.append(f"desc_richtext: 更新富文本 (multimedia_id={current_multimedia_id})")
                    except Exception as e:
                        logger.warning("更新富文本失败: %s", e)
                        # 尝试创建新的
                        try:
                            new_mm_id = self._create_fulltext(desc_richtext)
                            updated_info["multimedia_id"] = int(new_mm_id) if new_mm_id else 0
                            changes.append(f"desc_richtext: 创建新富文本 (multimedia_id={new_mm_id})")
                        except Exception as e2:
                            logger.warning("创建新富文本也失败: %s", e2)
                else:
                    try:
                        new_mm_id = self._create_fulltext(desc_richtext)
                        updated_info["multimedia_id"] = int(new_mm_id) if new_mm_id else 0
                        setup["advance"] = 1
                        changes.append(f"desc_richtext: 创建富文本 (multimedia_id={new_mm_id})")
                    except Exception as e:
                        logger.warning("创建富文本失败: %s", e)
            else:
                # 清除富文本说明
                if current_multimedia_id and current_multimedia_id != "0":
                    updated_info["multimedia_id"] = 0
                    setup["advance"] = 0
                    changes.append("desc_richtext: 清除富文本说明")

        # 4. 应用 session 级别的变更
        if session_title is not None and session_title != updated_info.get("sessionTitle"):
            updated_info["sessionTitle"] = session_title
            changes.append(f"sessionTitle: {session_title}")

        if auto_check is not None:
            new_val = 1 if auto_check else 0
            if new_val != updated_info.get("autoCheck"):
                updated_info["autoCheck"] = new_val
                changes.append(f"autoCheck: {auto_check}")

        if is_required is not None:
            new_val = 1 if is_required else 0
            if new_val != updated_info.get("is_require"):
                updated_info["is_require"] = new_val
                changes.append(f"is_require: {is_required}")

        if point_ratio is not None and point_ratio != updated_info.get("point_ratio"):
            updated_info["point_ratio"] = point_ratio
            changes.append(f"point_ratio: {point_ratio}")

        # 5. 应用 setup 级别的变更
        if is_anti_fraud is not None:
            new_val = "1" if is_anti_fraud else "0"
            if new_val != setup.get("is_anti_fraud"):
                setup["is_anti_fraud"] = new_val
                changes.append(f"is_anti_fraud: {is_anti_fraud}")

        if mini_program_switch is not None:
            new_val = "1" if mini_program_switch else "0"
            if new_val != setup.get("mini_program_switch"):
                setup["mini_program_switch"] = new_val
                changes.append(f"mini_program_switch: {mini_program_switch}")

        if share_status is not None:
            new_val = str(share_status)
            if new_val != setup.get("shareStatus"):
                setup["shareStatus"] = new_val
                changes.append(f"shareStatus: {share_status}")

        if result_prompt is not None:
            if result_prompt:
                setup["resultPrompt"] = result_prompt
                changes.append(f"resultPrompt: {result_prompt}")
            elif "resultPrompt" in setup:
                del setup["resultPrompt"]
                changes.append("resultPrompt: 清除")

        if type_name is not None:
            setup["type_name"] = type_name
            changes.append(f"type_name: {type_name}")

        # 只要修改了签到信息，就确保开启高级模式
        if signin_info_list is not None:
            if str(setup.get("advance", "")) != "1":
                setup["advance"] = 1
                changes.append("advance: 1")

        # 6. 处理签到信息（sectionArr）变更
        new_section_arr: list[dict[str, Any]] = []

        if signin_info_list is not None:
            cid_counter = 0

            def _next_cid() -> str:
                nonlocal cid_counter
                cid = f"c_{int(time.time() * 1000)}_{cid_counter}"
                cid_counter += 1
                return cid

            for idx, info in enumerate(signin_info_list):
                info_type = info.get("type", "").lower()
                title = info.get("title", "")
                required = bool(info.get("required", False))

                # 尝试匹配现有信息（按位置）
                existing_section = existing_sections[idx] if idx < len(existing_sections) else None
                existing_qinfo = existing_section.get("questionInfo", {}) if existing_section else {}
                existing_dom_type = existing_qinfo.get("domType", "")
                existing_qid = str(existing_qinfo.get("questionId", ""))
                existing_answers = existing_section.get("answerArr", []) if existing_section else []

                # 如果 domType 不同，不保留 ID
                preserve_id = False
                if existing_dom_type and existing_dom_type == info_type:
                    preserve_id = True
                elif existing_dom_type:
                    existing_qid = ""
                    existing_answers = []

                if info_type == "textarea":
                    hint = info.get("hint", "")
                    answer_arr = [{"answerContent": hint}] if hint else [{"answerContent": ""}]
                    # 保留原有 answerId（如果存在）
                    if preserve_id and existing_answers:
                        for i, ans in enumerate(answer_arr):
                            if i < len(existing_answers):
                                ans["answerId"] = str(existing_answers[i].get("answerId", ""))
                                ans["questionId"] = existing_qid

                    question_info = {
                        "extend": {"pic_url": []},
                        "setup": {"required": "0" if required else "1"},
                        "questionId": existing_qid if preserve_id else "",
                        "questionTitle": title,
                        "mobileQuestionTitle": title,
                        "pattern": "3",
                        "required": False,
                        "domType": "textarea",
                        "templateId": "",
                        "questionIndex": str(idx + 1),
                        "cid": _next_cid(),
                    }
                    new_section_arr.append({"questionInfo": question_info, "answerArr": answer_arr})

                elif info_type == "radio":
                    options = info.get("options", [])
                    if not options:
                        raise ValueError(f"第 {idx + 1} 题（单选）必须提供 options")

                    answer_arr = []
                    for opt_idx, opt in enumerate(options):
                        old_answer = existing_answers[opt_idx] if opt_idx < len(existing_answers) else {}
                        answer_arr.append({
                            "answerContent": str(opt),
                            "answerId": str(old_answer.get("answerId", "")) if old_answer else "",
                            "questionId": existing_qid if preserve_id else "",
                            "extend": {"pic_url": []},
                        })

                    question_info = {
                        "extend": {"pic_url": []},
                        "setup": {"required": "0" if required else "1"},
                        "questionId": existing_qid if preserve_id else "",
                        "questionTitle": title,
                        "mobileQuestionTitle": title,
                        "pattern": "0",
                        "required": False,
                        "domType": "radio",
                        "templateId": "",
                        "questionIndex": str(idx + 1),
                        "cid": _next_cid(),
                    }
                    new_section_arr.append({"questionInfo": question_info, "answerArr": answer_arr})

                elif info_type == "checkbox":
                    options = info.get("options", [])
                    if not options:
                        raise ValueError(f"第 {idx + 1} 题（多选）必须提供 options")

                    min_opts = info.get("min_options", 0)
                    max_opts = info.get("max_options", 0)

                    answer_arr = []
                    for opt_idx, opt in enumerate(options):
                        old_answer = existing_answers[opt_idx] if opt_idx < len(existing_answers) else {}
                        answer_arr.append({
                            "answerContent": str(opt),
                            "answerId": str(old_answer.get("answerId", "")) if old_answer else "",
                            "questionId": existing_qid if preserve_id else "",
                            "type": 0,
                            "extend": {"pic_url": []},
                            "isFocus": False,
                        })
                    # 空白选项
                    answer_arr.append({
                        "answerContent": "",
                        "answerId": "",
                        "questionId": existing_qid if preserve_id else "",
                        "type": 0,
                        "extend": {"pic_url": []},
                    })

                    setup_q = {"required": "0" if required else "1"}
                    if min_opts > 0:
                        setup_q["limitOptionsMin"] = min_opts
                    if max_opts > 0:
                        setup_q["limitOptionsMax"] = max_opts

                    question_info = {
                        "extend": {"pic_url": []},
                        "setup": setup_q,
                        "questionId": existing_qid if preserve_id else "",
                        "questionTitle": title,
                        "mobileQuestionTitle": title,
                        "pattern": "1",
                        "required": False,
                        "domType": "checkbox",
                        "templateId": "",
                        "questionIndex": str(idx + 1),
                        "cid": _next_cid(),
                    }
                    new_section_arr.append({"questionInfo": question_info, "answerArr": answer_arr})

                elif info_type == "number":
                    # 数值题（滑块/数值输入）
                    min_value = int(info.get("min", 0))
                    max_value = int(info.get("max", 100))
                    default_value = int(info.get("default", min_value))

                    question_info = {
                        "extend": {"pic_url": [], "min": min_value, "max": max_value},
                        "setup": {
                            "required": "0" if required else "1",
                            "defaultValue": default_value,
                        },
                        "questionId": existing_qid if preserve_id else "",
                        "questionTitle": title,
                        "mobileQuestionTitle": title,
                        "pattern": "8",
                        "required": False,
                        "domType": "number",
                        "templateId": "",
                        "questionIndex": str(idx + 1),
                        "cid": _next_cid(),
                    }
                    new_section_arr.append({"questionInfo": question_info, "answerArr": []})

                elif info_type == "paragraph":
                    content = info.get("content", "")
                    question_info = {
                        "questionId": existing_qid if preserve_id else "",
                        "sessionId": session_id if preserve_id else "",
                        "questionTitle": "",
                        "questionIndex": "",
                        "pattern": "4",
                        "required": "",
                        "creatTime": "",
                        "creatTimeShow": "",
                        "domType": "paragraph",
                        "totalCount": "",
                        "showType": {},
                        "showIndex": 0,
                        "setup": {},
                        "extend": {},
                        "cid": _next_cid(),
                        "desc": content,
                    }
                    new_section_arr.append({"questionInfo": question_info, "answerArr": []})

                else:
                    raise ValueError(f"不支持的签到信息类型: {info_type}（第 {idx + 1} 题）")

            # 保留未修改的剩余信息（如果新数组比旧数组长已在上面处理，这里处理旧数组更长的情况）
            # 实际上我们完全替换 sectionArr
            updated_info["sectionArr"] = new_section_arr
            changes.append(f"signin_info_list: 更新了 {len(signin_info_list)} 个签到信息")
        else:
            # 不修改签到信息，但保留原有 sectionArr
            # 过滤 sectionArr 中可能存在的只读字段
            new_section_arr = []
            for sec in existing_sections:
                new_sec = copy.deepcopy(sec)
                # 保留必要的字段
                new_section_arr.append(new_sec)
            updated_info["sectionArr"] = new_section_arr

        # 7. 处理 tags
        if tags is not None:
            updated_info["tags"] = [{"tag": str(tag)} for tag in tags]
            changes.append(f"tags: {tags}")

        # 8. 构造 session_data
        session_data = {
            "sessionInfo": updated_info,
            "sectionArr": updated_info.pop("sectionArr", []),
        }

        # 9. 调用 savesession（编辑模式，必须带 session_id）
        resp = self.client.post(
            self.client.desktop_url("/api/session/savesession"),
            data={
                "group_id": group_id,
                "session_id": session_id,
                "session_data": json.dumps(session_data, ensure_ascii=False),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            logger.error("更新签到小节失败: %s", resp.get("error", "unknown"))
            raise RuntimeError(f"更新签到小节失败: {resp.get('error', 'unknown')}")

        logger.info(
            "签到小节更新成功: session_id=%s, changes=%s",
            session_id, changes,
        )

        return {
            "session_id": session_id,
            "group_id": group_id,
            "changes": changes,
        }

    # ------------------------------------------------------------------
    # 删除小节
    # ------------------------------------------------------------------

    def delete_session(
        self,
        group_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        """删除课程中的小节.

        先验证小节存在于课程中，然后调用删除 API。

        Args:
            group_id: 课程 ID
            session_id: 小节 ID

        Returns:
            包含 session_id 和 deleted 标志的字典

        Raises:
            RuntimeError: 删除失败或小节不存在
        """
        # 1. 验证小节存在于课程中
        self._get_session_from_course(group_id, session_id)

        # 2. 调用删除 API
        resp = self.client.post(
            self.client.desktop_url("/ajax/e_deleteSession"),
            data={
                "groupId": group_id,
                "sessionId": session_id,
            },
        )

        if resp.get("status") not in (True, "true"):
            raise RuntimeError(
                f"删除小节失败: {resp.get('error', 'unknown')}"
            )

        data = resp.get("data", {})
        success = data.get("success", 0)
        success_list = data.get("success_list", [])

        if success == 0 or str(session_id) not in [str(s) for s in success_list]:
            raise RuntimeError(
                f"删除小节未成功: success={success}, success_list={success_list}"
            )

        logger.info("小节删除成功: session_id=%s, group_id=%s", session_id, group_id)

        return {
            "session_id": session_id,
            "group_id": group_id,
            "deleted": True,
        }
