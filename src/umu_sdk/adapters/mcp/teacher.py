# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""UMU 讲师端 MCP Server.

将 UMU 平台的讲师资源管理操作暴露为 MCP Tools，供 AI 自主编排课程创建流程。

Usage:
    # 启动 MCP Server（默认）
    python -m umu_sdk.mcp.server_teacher

    # 或使用 CLI
    umu-mcp-teacher

Environment Variables:
    UMU_BASE_URL: UMU 基础 URL (默认: https://www.umu.cn)
    UMU_TEACHER_USERNAME: 讲师登录用户名
    UMU_TEACHER_PASSWORD: 讲师登录密码
    MCP_LOG_LEVEL: 日志级别 (DEBUG|INFO|WARNING|ERROR，默认: INFO)
"""

from __future__ import annotations

# Windows 中文编码修复 —— 必须在所有导入之前执行
import io
import sys

if sys.platform == "win32":
    try:
        if isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if isinstance(sys.stderr, io.TextIOWrapper):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ...core.client import UMUClient
from ...core.credential_loader import load_credentials_with_source
from .utils import (
    _format_auto_close_tips,
    _parse_auto_close_time,
    format_login_summary,
    fuzzy_filter_items,
    fuzzy_filter_items_multi_key,
    get_login_identity,
    report_pagination_progress,
)
from .cos_upload import (
    ScormUploader,
    UploadResult,
    validate_file_path,
)
from .course_builder import CourseBuilder
from .document_upload import (
    DocumentUploader,
    validate_document_path,
)
from .export_engine import ExportEngine
from .image_upload import ImageUploader
from .program_builder import ProgramBuilder
from .program_student_manager import ProgramStudentManager
from .session import SessionManager
from .video_upload import (
    VideoUploader,
    validate_video_path,
    VIDEO_MEDIA_TYPE,
)
from .shared_access_permissions import (
    _add_obj_access_accounts,
    _cancel_all_assigned_permissions,
    _format_access_account,
    _get_obj_access_list,
    _get_obj_access_permission,
    _parse_access_permission_response as _parse_collaboration_response,
    _permission_text,
    _remove_obj_access_accounts,
    _search_access_permission_account,
    _set_obj_access_permission,
)
from .shared_session_tools import (
    SessionToolConfig,
    make_check_auth_tool,
    make_create_session_tool,
    make_destroy_session_tool,
    make_list_sessions_tool,
    make_login_tool,
)

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

    root = logging.getLogger("umu.mcp.teacher")
    root.setLevel(level)
    root.handlers = [handler]


_setup_logging()
logger = logging.getLogger("umu.mcp.teacher")

# ---------------------------------------------------------------------------
# 全局实例（由 lifespan 管理）
# ---------------------------------------------------------------------------
_umu_client: UMUClient | None = None
_session_manager: SessionManager | None = None


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """应用生命周期管理.

    启动时初始化会话管理器并创建默认会话；关闭时释放所有会话资源.
    默认从 UMU_TEACHER_USERNAME / UMU_TEACHER_PASSWORD 读取讲师账号自动登录.
    未配置凭据时正常启动，提示手动调用 tch_login 登录.
    """
    global _umu_client, _session_manager

    base_url = os.getenv("UMU_BASE_URL", "https://www.umu.cn")
    # 每次启动都重新读取讲师账号凭据；优先级：显式参数/环境变量 > .env > 加密凭证
    username, password, source = load_credentials_with_source("teacher")

    _session_manager = SessionManager(
        base_url=base_url,
    )

    default_session = await _session_manager.create_session()
    _umu_client = default_session.client

    if username and password:
        try:
            await _session_manager.login_session(
                default_session.session_id, username, password, credential_source=source.value
            )
            default_session.credential_source = source.value
            identity = get_login_identity(_umu_client)
            logger.info(
                "默认会话已自动登录: %s",
                format_login_summary(username, source.value, identity),
            )
        except Exception as e:
            logger.error("默认会话自动登录失败: %s", e)
    else:
        logger.info("未配置讲师账号凭据，请调用 tch_login 或 tch_create_session")

    logger.info(
        "UMU 讲师端服务已启动，目标: %s",
        base_url,
    )

    yield {"client": _umu_client, "session_manager": _session_manager}

    if _session_manager:
        _session_manager.close_all()
        _session_manager = None
    _umu_client = None
    logger.info("UMU 讲师端服务已关闭")


# ---------------------------------------------------------------------------
# 创建 MCP 服务器
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "umu-teacher",
    instructions="""UMU 学习平台讲师端 MCP 服务。

提供讲师课程管理和资源管理相关的原子化操作，包括：
- 创建空课程（支持封面/背景图上传、富文本介绍、分类/标签/时间设置）
- 获取课程分类树（动态获取当前账号可用的所有分类，带 5 分钟缓存）
- 获取已创建的课程列表（支持分页、按更新时间/创建时间排序）
- 获取协同给我的课程列表（支持分页、排序）
- 获取我参与的课程列表（支持按学习状态筛选：已学习/学习中/待学习）
- 列出课程协同者（含角色、协同关系 ID）：tch_list_course_collaborators
- 搜索可协同账号（按邮箱/姓名/用户名/手机号）：tch_search_collaborator_accounts
- 邀请/调整协同者权限（editor/operator/viewer）：tch_invite_course_collaborator / tch_update_collaborator_role
- 删除课程协同者：tch_remove_course_collaborator
- 转让课程拥有者：tch_transfer_course_owner
- 获取课程信息（查看当前配置，过滤只读字段）
- 综合修改课程信息（tch_update_course，适合一次改多个字段）
- 原子化修改（6 个细粒度工具，适合只改一个维度）：
  - tch_update_course_basic — 标题、描述、备注、标签
  - tch_update_course_type — 课程形式（线上/面授/混合）
  - tch_update_course_category — 分类（支持名称/路径匹配）
  - tch_update_course_schedule — 有效期和上课时段（语义化时间参数）
  - tch_update_course_images — 封面图/背景图上传替换
  - tch_update_course_richtext — 富文本介绍（含图片上传）
- 设置课程报名（tch_set_course_enrollment）：开启/关闭报名、自动审核、报名名额等
  - 注意：UMU 报名开关不走 e_saveGroup，本工具已封装独立的 /api/enroll/saveenroll 调用
- 提交课程至企业知识库审核（tch_submit_course_for_audit，管理员审核后可被推荐和搜索）
- 添加 SCORM 小节到课程（支持使用已有资源或上传新 SCORM 包）
- 列出课程小节（获取所有小节的 ID、标题、资源绑定等信息）
- 获取单个小节详情（查看当前状态，过滤只读统计字段）
- 修改 SCORM 小节（更换资源、修改标题、调整必修/选修状态等）
- 删除课程小节
- SCORM 课程包上传（获取凭证 → COS 直传 → 注册 → 轮询状态）
- 文档上传（Excel/Word/PPT/PDF，支持小文件直传和大文件分片上传，支持幂等性保护）
- 批量文档上传（tch_upload_documents_batch，支持前置路径校验和 skip_existing）
- 文档列表查询（分页、搜索，size=0 自动标记）
- 文档重命名
- 文档删除（支持引用警告）
- 批量文档删除（tch_delete_documents_batch，逐个检查引用状态）
- 音视频上传（支持 36 种音视频格式，小文件直传 + 大文件分片上传）
- 音视频资源列表查询（分页、搜索）
- 音视频资源重命名
- 音视频资源删除

AI 使用本服务时，应先确保讲师已登录，然后按需求调用对应工具。

【创建课程 + SCORM 小节的标准流程】
1. 调用 tch_login 登录讲师账号
2. (可选) 调用 tch_get_categories 获取当前账号可用的课程分类
3. 调用 tch_create_course 创建空课程（获得 group_id）
   - 分类可用 category_ids（ID 列表）或 category_names（名称/路径列表）
4. 调用 tch_create_scorm_section 添加 SCORM 小节：
   - 方式 A：提供 scorm_resource_id（从 tch_list_resources 获取的已有资源）
   - 方式 B：提供 scorm_file_path（上传新的 SCORM 包）

【创建课程 + 文档小节的标准流程】
1. 调用 tch_login 登录讲师账号
2. (可选) 调用 tch_get_categories 获取当前账号可用的课程分类
3. 调用 tch_create_course 创建空课程（获得 group_id）
4. 准备文档资源（二选一）：
   - 方式 A：提供 document_resource_id（从 tch_list_documents 获取的已有文档）
   - 方式 B：先调用 tch_upload_document 上传新文档，获取 resource_id
     - 支持格式：.ppt/.pptx/.xls/.xlsx/.doc/.docx/.pdf/.txt/.xlsm
     - 文件大小限制 100MB
5. 调用 tch_create_document_section 添加文档小节：
   - 必需：group_id, section_title, document_resource_id（或 document_file_path）
   - 文档说明：desc_plain（纯文本）或 desc_richtext（HTML 富文本）
   - 学习设置：is_required, allow_download, min_duration_minutes, finish_condition
   - 互动设置：enable_comment, show_comment_time, show_creator_info
   - 标签：tags 列表

【文档小节参数详解】
- is_required: True=必修, False=选修
- allow_download: True=允许学员下载文档, False=不允许
- min_duration_minutes: 最小学习时长（分钟），0=不限制
  - 学员需学习达到此时长才算完成（配合 finish_condition 使用）
- finish_condition: "open"=打开文档即完成（适合快速浏览类文档）
                  "last_page"=学完文档最后一页才算完成（适合需要完整阅读的内容）
- enable_comment: True=开启发言区（学员可评论）, False=关闭
- show_comment_time: True=学员可查看每条发言的提交时间
- show_creator_info: True=在小节中展示课程创建者信息
- tags: 标签列表，如 ["培训", "必修"]

【创建课程 + 考试小节的标准流程】
1. 调用 tch_login 登录讲师账号
2. (可选) 调用 tch_get_categories 获取当前账号可用的课程分类
3. 调用 tch_create_course 创建空课程（获得 group_id）
4. 准备题目数据（questions_json JSON 字符串）：
   - 单选题: {"type":"radio","title":"...","score":5,"options":["A","B","C"],"correct_indices":[1]}
   - 多选题(全部正确得分): {"type":"checkbox","title":"...","score":7,"options":["A","B","C","D"],"correct_indices":[0,1,2],"scoring_rule":"all_correct"}
   - 多选题(部分正确得分): {"type":"checkbox","title":"...","score":10,"options":["A","B","C","D"],"correct_indices":[0,1,2],"scoring_rule":"partial","partial_score":6}
   - 开放题(自动评分): {"type":"input","title":"...","score":10,"standard_answers":["答案1","答案2"]}
   - 开放题(手动评分): {"type":"input","title":"...","score":10}  # 不设置 standard_answers
5. 调用 tch_create_exam_section 创建考试小节

【修改考试小节的标准流程】
1. 调用 tch_login 登录讲师账号
2. 调用 tch_get_course_detail 获取课程详情（含小节列表和 session_id）
3. 调用 tch_update_exam_section 修改考试小节设置：
   - 所有参数均为可选，传入 None 表示不修改
   - 常见修改场景：
     - 修改考试时长：exam_duration_minutes=20
     - 修改及格线：quiz_pass_mark=60
     - 修改访问权限：share_status=0（关闭）
     - 修改展示样式：question_show_mode="1"（逐题式）
     - 修改是否必修：is_required=False
   - 如需同时修改题目，提供完整的 questions_json

【创建课程 + 签到小节的标准流程】
1. 调用 tch_login 登录讲师账号
2. (可选) 调用 tch_get_categories 获取当前账号可用的课程分类
3. 调用 tch_create_course 创建空课程（获得 group_id）
4. 准备签到信息数据（signin_info_json JSON 字符串）：
   - 文本输入: {"type":"textarea","title":"您的姓名是？","required":true,"hint":"请输入姓名"}
   - 单选题: {"type":"radio","title":"您的性别是？","required":true,"options":["女","男"]}
   - 多选题: {"type":"checkbox","title":"谁是你的朋友？","required":true,"options":["黄飞鸿","洪七公","周伯通"],"min_options":1,"max_options":2}
   - 段落说明: {"type":"paragraph","content":"<p>说明文字</p>"}
5. 调用 tch_create_signin_section 创建签到小节：
   - 必需：group_id, session_title, signin_info_json
   - 设置：auto_check, is_required, point_ratio, is_anti_fraud
   - 权限：share_status, mini_program_switch
   - 提示：result_prompt（如"签到成功！"）
   - 说明：desc_richtext（HTML 富文本）

【修改签到小节的标准流程】
1. 调用 tch_login 登录讲师账号
2. 调用 tch_get_course_detail 获取课程详情（含小节列表和 session_id）
3. 调用 tch_update_signin_section 修改签到小节：
   - 所有参数均为可选，传入 None 表示不修改
   - 常见修改场景：
     - 修改标题：session_title="新的签到标题"
     - 修改签到信息：signin_info_json（提供完整列表，按索引位置匹配更新）
     - 改为手动审核：auto_check=False
     - 修改是否必修：is_required=False
     - 修改成功提示：result_prompt="恭喜您签到成功！"
     - 修改签到说明：desc_richtext="<p>新的说明内容</p>"（空字符串清除）
     - 修改标签：tags=["签到", "必修"]

【创建课程 + 图文小节的标准流程】
1. 调用 tch_login 登录讲师账号
2. (可选) 调用 tch_get_categories 获取当前账号可用的课程分类
3. 调用 tch_create_course 创建空课程（获得 group_id）
4. 准备图文内容：
   - 图片：本地路径（自动上传）或已上传的 URL
   - 文字：纯文本内容
   - 内容块格式：[{"type": "image", "content": "..."}, {"type": "text", "content": "..."}]
5. 调用 tch_create_infographic_section 添加图文小节：
   - 必需：group_id, session_title, content_blocks
   - 封面：cover_image_path（本地路径）或 cover_resource_id（已上传资源ID）
   - 学习设置：is_required, min_duration_seconds, max_duration_seconds
   - 互动设置：enable_comment, is_comment_time_visible, show_course_creator_info
   - 标签：tags 列表

【图文小节参数详解】
- content_blocks: 图文内容块列表
  - {"type": "image", "content": "图片路径或URL"} — 图片块
  - {"type": "text", "content": "文字内容"} — 文字块
  - 图片支持本地路径自动上传（media_type="image"）
- min_duration_seconds: 最小学习时长（秒），0=不限制
- max_duration_seconds: 学习时长统计上限（秒），0=不限制
- enable_comment: True=开启发言区，False=关闭
- is_comment_time_visible: True=学员可查看发言提交时间
- show_course_creator_info: True=展示课程创建者信息

【文档格式说明】
支持的格式：.ppt/.pptx/.xls/.xlsx/.doc/.docx/.pdf/.txt/.xlsm
文件大小限制：100MB（超过会返回 FILE_TOO_LARGE 错误）

【文档说明模式选择】
- 纯文本（desc_plain）：简单文字说明，适合简短描述
- 富文本（desc_richtext）：支持 HTML 格式，可包含图片、链接、格式化文字
  - 不要同时提供 desc_plain 和 desc_richtext

【小节类型速查 — sessionType 码值映射】
list_sections 返回的每个小节包含 type 字段，对应关系如下：

有 MCP tool 支持的类型：
- type="survey" → 问卷小节（sessionType=1）→ 用 tch_create_survey_section / tch_update_survey_section
- type="exam" → 考试小节（sessionType=10）→ 用 tch_create_exam_section / tch_update_exam_section
- type="video" → 视频微课小节（sessionType=11）→ 用 tch_create_video_section / tch_update_video_section
- type="scorm" → SCORM 小节（sessionType=11，与视频共用码值）→ 用 tch_create_scorm_section / tch_update_scorm_section
- type="article" → 文章小节（sessionType=13）→ 用 tch_create_article_section / tch_update_article_section
- type="document" → 文档小节（sessionType=14）→ 用 tch_create_document_section / tch_update_document_section
- type="infographic" → 图文小节（sessionType=15）→ 用 tch_create_infographic_section / tch_update_infographic_section，用 tch_get_infographic_content 查看内容
- type="signin" → 签到小节（sessionType=6）→ 用 tch_create_signin_section / tch_update_signin_section

暂不支持 MCP tool 的类型（可通过 UMU Web 端创建）：
- sessionType=2 → 提问（Q&A）
- sessionType=3 → 讨论（Discussion）
- sessionType=4 → 拍照上墙（Flipchart Slide）
- sessionType=5 → 游戏（Game）
- sessionType=7 → 语音微课（Audio Slides）
- sessionType=8 → 抽奖（Raffle Drawing）
- sessionType=12 → 直播微课（Video Live，已不再使用）
- sessionType=16 → 作业（Exercise）
- sessionType=17 → 会议（Meeting）
- sessionType=18 → 直播（Live）
- sessionType=19 → AI微课（AI Video）
- sessionType=20 → 阶段点评（Evaluation）

【修改小节的标准流程】
1. 调用 tch_login 登录讲师账号
2. 调用 tch_list_sections(group_id) 获取小节列表，查看每个小节的 type 字段判断类型
3. （可选）调用 tch_get_section(section_id) 查看当前详情
4. 根据类型调用对应的修改工具：

   SCORM 小节 → tch_update_scorm_section：
   - 更换 SCORM 资源：scorm_resource_id / scorm_file_path
   - 修改标题：section_title
   - 调整必修/选修：is_required
   - 调整学习时长：duration_minutes
   - 更换封面：section_cover_path

   文档小节 → tch_update_document_section：
   - 更换文档资源：document_resource_id / document_file_path
   - 修改标题：section_title
   - 修改文档说明：desc_plain（纯文本）或 desc_richtext（HTML）
   - 调整必修/选修：is_required
   - 调整下载权限：allow_download
   - 调整学习时长：min_duration_minutes（分钟）
   - 调整完成条件：finish_condition（"open"/"last_page"）
   - 调整发言区：enable_comment / show_comment_time
   - 调整标签：tags
   - 更换封面：section_cover_path

   图文小节 → tch_update_infographic_section：
   - 【重要】修改内容时必须提供 resource_imgText_id（创建时返回，需保存）
   - 修改标题：session_title
   - 修改内容：content_blocks（图文块列表，每项 {"type": "image"|"text", "content": "..."}）
   - 查看内容：tch_get_infographic_content(resource_imgText_id)
   - 调整必修/选修：is_required
   - 调整学习时长：min_duration_seconds / max_duration_seconds
   - 调整发言区：enable_comment / is_comment_time_visible
   - 调整标签：tags
   - 更换封面：cover_image_path / cover_resource_id
   - 移除封面：remove_cover=True

   考试小节 → tch_update_exam_section：
   - 修改标题：session_title
   - 修改题目：questions_json（提供完整题目列表）
   - 修改考试时长：exam_duration_minutes
   - 修改及格线：quiz_pass_mark
   - 修改访问权限：share_status
   - 修改是否必修：is_required
   - 修改积分倍率：point_ratio

   签到小节 → tch_update_signin_section：
   - 修改标题：session_title
   - 修改签到信息：signin_info_json（提供完整信息列表，按索引匹配更新）
   - 修改审核方式：auto_check（True=自动, False=手动）
   - 修改是否必修：is_required
   - 修改防作弊：is_anti_fraud
   - 修改小程序开关：mini_program_switch
   - 修改访问权限：share_status
   - 修改成功提示：result_prompt
   - 修改签到说明：desc_richtext（HTML，空字符串清除）
   - 修改标签：tags

5. 可同时修改多个字段（在一个调用中传入所有要改的字段）
6. 如需控制学员可见性（打开/关闭），调用 tch_toggle_section_visibility（通用，不限小节类型）
7. 如需删除小节，调用 tch_delete_section(group_id, section_id)

【常见操作组合】
- 修改标题+设为必修：调用对应 update 工具，同时传入 section_title 和 is_required=True
- 修改后关闭学员可见：先调用 update 工具修改内容，再调用 tch_toggle_section_visibility 关闭
- 只改一个字段：调用对应 update 工具，只传入该字段，其余保持原值

【创建含多个小节的课程的推荐流程】
1. 调用 tch_login 登录讲师账号
2. (可选) 调用 tch_get_categories 获取可用课程分类
3. 调用 tch_create_course 创建空课程（获得 group_id）
4. 依次创建各个小节（考试、问卷、签到、文档、图文等）：
   - 每成功创建一个小节，记录返回的 session_id
   - 各小节创建相互独立，前面成功的不影响后续创建
5. 全部创建完成后，调用 tch_get_course_detail 验证课程完整性

【创建失败的恢复指南】
小节创建是原子操作，每个小节独立创建。如果某个小节创建失败：

情况 A — 请求完全失败（返回错误，无 session_id）：
- 该小节实际上没有创建成功，系统中无残留
- 直接修正参数后重新调用创建工具即可

情况 B — 部分成功（返回了 session_id，但内容不完整/有误）：
- 系统中留下了一个不完整的小节
- 调用 tch_delete_section(group_id, session_id) 删除它
- 修正参数后重新创建

情况 C — 创建后发现配置有误：
- 如果小节类型支持修改 → 调用对应的 tch_update_*_section 工具修改
- 如果不支持修改 → 调用 tch_delete_section 删除后重新创建

重要原则：
- 某小节创建失败不会导致已创建的其他小节失效
- 前面成功的小节保留不动，继续创建剩余小节
- 如需删除整个课程重新来过，可逐个调用 tch_delete_section 删除所有小节

【修改课程的标准流程 — 原子化工具（推荐）】
1. 调用 tch_login 登录讲师账号
2. 调用 tch_get_course(group_id) 获取课程当前配置
3. 根据要修改的内容选择对应的原子化工具：
   - 改标题/描述 → tch_update_course_basic
   - 改课程形式 → tch_update_course_type
   - 改分类 → tch_update_course_category
   - 改时间 → tch_update_course_schedule
   - 改图片 → tch_update_course_images
   - 改富文本 → tch_update_course_richtext
4. 再次调用 tch_get_course 验证修改结果

【综合修改（tch_update_course）】
- 当需要同时修改多个不同维度的字段时，可使用 tch_update_course
- 参数更多但更复杂，建议优先使用原子化工具

课程形式(lesson_type)取值：0=线上课程, 1=面授培训, 2=混合式课程, 999=其他
分类设置：不同账号的分类树不同，请先调用 tch_get_categories 查看可用分类
          支持用完整路径指定分类，如 "课程系列 > 新能力系列 > 客户思维"
          同名分类可能有歧义，建议使用完整路径

【封面图注意事项】
- 课程封面和小节封面使用独立的资源上传流程
- 小节封面在 savesession 时即预置到 sectionArr.questionInfo.extend 中
- 前端从 questionInfo.extend.custom_cover_resource_id 读取封面，不是从 bind-upd
- 如小节封面未显示，检查 resourceCallback 是否成功注册（getresourceinfo 应返回 info 对象）

【文档管理（我的文档）的标准流程】
1. 调用 tch_login 登录讲师账号
2. 上传文档：
   - 单文件：tch_upload_document(file_path, name, skip_existing=False)
   - 批量：tch_upload_documents_batch(file_paths, skip_existing=False)
   - skip_existing=True 时，上传前会根据文件名+大小查重，已存在的自动跳过
   - 支持格式：.xlsx/.xls, .docx/.doc, .pptx/.ppt, .pdf, .txt
   - 小文件（<50MB）直接上传，大文件自动分片并发上传
   - 上传成功后返回 resource_id 和 next_actions（包含改名/删除的命令模板）
3. 调用 tch_list_documents 查看文档列表（分页、搜索）
4. 调用 tch_rename_document(resource_id, file_name) 修改名称
5. 删除文档：
   - 单文件：tch_delete_document(resource_id)
   - 批量：tch_delete_documents_batch(resource_ids)
   - 删除前会自动检查是否被课程小节引用，被引用的会返回 HIGH 级别警告

文档上传完成后可用于课程小节的资源绑定。

【音视频管理（我的音视频）的标准流程】
1. 调用 tch_login 登录讲师账号
2. 上传音视频：
   - 调用 tch_upload_audio_video(file_path, name)
   - 支持 36 种音视频格式：mp4, mov, avi, mkv, mp3, wav, flac 等
   - 文件大小限制 1024MB（1GB）
   - 小文件（<50MB）直接上传，大文件自动分片并发上传
   - 上传成功后返回 resource_id 和 next_actions
3. 调用 tch_list_audio_videos 查看音视频列表（分页、搜索）
4. 调用 tch_rename_audio_video(resource_id, file_name) 修改名称
5. 删除音视频：
   - 调用 tch_delete_audio_video(resource_id)
   - 删除前会自动检查是否被课程小节引用，被引用的会返回 HIGH 级别警告

音视频上传完成后可用于创建视频/音频小节。

【常见后端错误码速查】
调用 API 返回的错误码（error_code）说明：

认证/请求类 (200xxx)：
- 200003 → token error：token 加密错误，请重新调用 tch_login 登录
- 200004 → limit request frequency：请求频率超出限制，请稍后再试
- 200005 → out of authorized ip list：请求IP不在授权列表中
- 200006 → unauthorized request：请求认证失败，token 可能已过期

通用业务类 (100xxx / 400xxx / 500xxx)：
- 100005 → group id empty：课程ID（group_id）参数为空
- 100008 → group does not exists：课程不存在，请检查 group_id 是否正确
- 100011 → request params (xxx) is not valid：请求参数不合法，请检查参数格式和类型
- 100014 → save failed：保存失败，可能是数据格式错误或服务端异常
- 100015 → this (xxx) is not in your enterprise：课程/小节/学习项目不属于当前企业
- 400001 → 添加失败：企业额度已满，无法创建新内容
- 500001 → element data does not exist：数据不存在或已被删除

课程类 (701xxx)：
- 701003 → no course title：课程标题为空
- 701004 → no course id or format error：课程ID为空或格式错误
- 701005 → course not exists in this enterprise：课程不存在于当前企业
- 701006 → lesson_type format error：课程形式（lesson_type）格式错误
- 701008 → category_ids format error：分类ID格式错误
- 701009 → tags format error：标签格式错误
- 701010 → course created successful but query failed：课程创建成功但查询失败（可忽略，课程已创建）
- 701013 → course title should between 0 and 200：课程标题长度需在 0-200 字符之间
- 701014 → course category_ids invalid：课程分类 ID 无效，请先调用 tch_get_categories 获取有效分类
- 701015 → course lesson type invalid：课程形式不合法
- 701016 → participant_number need great than 0：参与人数需大于 0
- 701017 → no permission to edit course：当前用户无权编辑该课程
- 701018 → user not allowed to create course：当前用户无创建课程权限
- 701019 → remark invalid：备注格式不合法
- 701020-701026 → 字段格式错误：省/市/区/地址/联系方式/客户名称等字段格式不合法
- 701027 → timespan invalid：时间段格式不合法
- 701028 → course_time invalid：课程时间格式不合法

权限/访问类 (703xxx)：
- 703001 → access_permission format error：访问权限格式错误
- 703005 → invalid course access permission：课程访问权限不合法
""",
    lifespan=app_lifespan,
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _get_client(session_id: str | None = None) -> UMUClient:
    """获取客户端实例."""
    if session_id:
        if _session_manager is None:
            raise RuntimeError("会话管理器未初始化")
        session = _session_manager.get_session_sync(session_id)
        if session is None:
            raise RuntimeError(f"会话不存在或已过期: {session_id}")
        return session.client

    if _umu_client is None:
        raise RuntimeError("UMU 客户端未初始化，请先登录")
    return _umu_client


def _require_auth(client: UMUClient) -> str | None:
    """检查客户端认证状态.

    Returns:
        None 表示认证正常；否则返回错误信息字符串.
    """
    if not client.auth.is_authenticated():
        return "当前未登录或 Token 已过期，请先调用 tch_login 登录"
    return None


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


_TEACHER_SESSION_CONFIG = SessionToolConfig(
    role="tch",
    role_label="讲师",
    tool_domain_hint="讲师端资源管理相关 Tool",
    login_success_suffix="现在可以调用讲师端资源管理相关 Tool",
    check_auth_success_suffix="讲师端 Tool",
    create_session_suggested_action="使用此 session_id 调用 tch_login 登录",
    create_session_with_password=False,
    isoformat_timestamps=True,
    session_manager_not_init_code="SESSION_MANAGER_NOT_INIT",
    create_session_failed_code="SESSION_CREATE_FAILED",
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _format_size(size_bytes: int) -> str:
    """格式化文件大小为人类可读字符串."""
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.2f} KB"
    else:
        return f"{size_bytes} B"


def _find_document_by_name_size(
    client: UMUClient, file_name: str, file_size: int
) -> str | None:
    """根据文件名和大小查找已存在的文档资源 ID（用于幂等性检查）.

    Returns:
        已存在的 resource_id，或 None（未找到）
    """
    try:
        resp = client.get(
            client.desktop_url("/ajax/resource/getresourcelist"),
            params={
                "page": "1",
                "is_recycle": "0",
                "search_keyword": file_name,
                "page_rows": "20",
                "order_by": "create_time",
                "is_desc": "1",
                "media_type": "docweike",
                "status_str": "in_use,transcoding,wait_transcoding",
            },
        )
        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            return None

        for item in resp.get("data", {}).get("list", []):
            existing_name = item.get("file_name", "")
            existing_size = int(item.get("file_size", 0) or 0)
            # 文件名和大小都匹配，认为是同一个文件
            if existing_name == file_name and existing_size == file_size:
                return str(item.get("id", ""))
        return None
    except Exception:
        return None


def _verify_resource_registered(
    client: UMUClient, resource_id: str, max_attempts: int = 3
) -> bool:
    """防御性验证：确认资源已成功注册到资源列表.

    Args:
        client: UMUClient 实例
        resource_id: 要验证的资源 ID
        max_attempts: 最大重试次数

    Returns:
        True 如果确认注册成功，False 如果无法确认
    """
    for attempt in range(max_attempts):
        try:
            # 方式 1: 通过 getresourceinfo 验证
            info_resp = client.get(
                client.desktop_url("/ajax/resource/getresourceinfo"),
                params={"resource_id": resource_id, "media_type": "docweike"},
            )
            if info_resp.get("status") is True or info_resp.get("error_code") == 0:
                info = info_resp.get("data", {}).get("info")
                if info:
                    return True

            # 方式 2: 通过 getresourcelist 验证
            list_resp = client.get(
                client.desktop_url("/ajax/resource/getresourcelist"),
                params={
                    "page": "1",
                    "is_recycle": "0",
                    "search_keyword": "",
                    "page_rows": "50",
                    "order_by": "create_time",
                    "is_desc": "1",
                    "media_type": "docweike",
                },
            )
            for item in list_resp.get("data", {}).get("list", []):
                if item.get("id") == resource_id:
                    return True

        except Exception as e:
            logger.warning("资源注册验证失败 (attempt %d/%d): %s", attempt + 1, max_attempts, e)

        if attempt < max_attempts - 1:
            time.sleep(0.5)

    return False


async def _upload_scorm_if_needed(
    client: UMUClient,
    scorm_file_path: str | None,
    scorm_resource_id: str | None,
    default_name: str,
) -> tuple[str | None, str | None]:
    """按需上传 SCORM，返回 (resource_id, error_response).

    如果提供了 scorm_resource_id，直接返回。
    如果提供了 scorm_file_path，上传后返回 resource_id。
    如果出错，返回 (None, error_json_string)。
    """
    if scorm_resource_id:
        return scorm_resource_id, None

    if not scorm_file_path:
        return None, None

    try:
        validate_file_path(scorm_file_path)
    except (FileNotFoundError, ValueError) as e:
        return None, _err(
            error_code="INVALID_FILE",
            error_message=str(e),
            suggested_action="请提供有效的 SCORM zip 文件路径",
        )

    uploader = ScormUploader(client, client.base_url)
    result = await uploader.run(scorm_file_path, name=default_name)

    if not result.resource_id:
        return None, _err(
            error_code="SCORM_UPLOAD_FAILED",
            error_message="SCORM 上传成功但返回的 resource_id 为空",
            suggested_action="请检查文件是否有效，或稍后重试",
        )

    logger.info("SCORM 上传成功: resource_id=%s", result.resource_id)
    return result.resource_id, None


# 文档小节文件大小限制：100MB
_MAX_DOCUMENT_SIZE_BYTES = 100 * 1024 * 1024


async def _upload_document_if_needed(
    client: UMUClient,
    document_file_path: str | None,
    document_resource_id: str | None,
    default_name: str,
) -> tuple[str | None, str | None]:
    """按需上传文档，返回 (resource_id, error_response).

    如果提供了 document_resource_id，直接返回。
    如果提供了 document_file_path，上传后返回 resource_id。
    如果出错，返回 (None, error_json_string)。
    文件大小限制 100MB。
    """
    if document_resource_id:
        return document_resource_id, None

    if not document_file_path:
        return None, None

    try:
        validate_document_path(document_file_path)
    except (FileNotFoundError, ValueError) as e:
        return None, _err(
            error_code="INVALID_FILE",
            error_message=str(e),
            suggested_action="请提供有效的文档文件路径",
        )

    # 检查文件大小限制（100MB）
    file_size = os.path.getsize(document_file_path)
    if file_size > _MAX_DOCUMENT_SIZE_BYTES:
        return None, _err(
            error_code="FILE_TOO_LARGE",
            error_message=f"文档文件大小 {file_size / (1024 * 1024):.2f}MB 超过限制 100MB",
            suggested_action="请压缩文档或分割为多个文件",
        )

    uploader = DocumentUploader(client, client.base_url)
    result = await uploader.run(document_file_path, name=default_name)

    if not result.resource_id:
        return None, _err(
            error_code="DOCUMENT_UPLOAD_FAILED",
            error_message="文档上传成功但返回的 resource_id 为空",
            suggested_action="请检查文件是否有效，或稍后重试",
        )

    logger.info("文档上传成功: resource_id=%s", result.resource_id)
    return result.resource_id, None


def _upload_image_if_needed(
    client: UMUClient,
    image_path: str | None,
    media_type: str = "picweike",
) -> tuple[str | None, str]:
    """按需上传图片，返回 (resource_id, error_message_or_empty).

    上传失败时返回 (None, error_message)，但不应阻止主流程。
    """
    if not image_path:
        return None, ""

    try:
        uploader = ImageUploader(client, client.base_url)
        result = uploader.upload(image_path, media_type=media_type)
        logger.info("图片上传成功: resource_id=%s", result.resource_id)
        return result.resource_id, ""
    except Exception as e:
        msg = str(e)
        logger.warning("图片上传失败（非致命）: %s", msg)
        return None, msg


# ---------------------------------------------------------------------------
# 课程协同辅助函数
# ---------------------------------------------------------------------------

_ROLE_TO_API: dict[str, str] = {
    "editor": "cooperator",
    "operator": "operator",
    "viewer": "viewer",
}

_API_ROLE_TO_LABEL: dict[str, str] = {
    "cooperator": "编辑者",
    "operator": "运营者",
    "viewer": "查看者",
    "creator": "拥有者",
}


def _map_role_to_api(role: str) -> str | None:
    """将面向用户的角色名映射为 UMU API 的 role_type."""
    return _ROLE_TO_API.get(role.lower())


def _search_collaborator_account(
    client: UMUClient,
    group_id: str,
    keyword: str,
) -> tuple[bool, list[dict[str, Any]], str]:
    """搜索可设置为协同者的账号.

    Returns:
        (success, accounts, error_message)
    """
    resp = client.post(
        client.desktop_url("/api/manage/accessaccountmatchv2"),
        data={
            "accounts": keyword,
            "search_source": "add_cooperator",
            "is_suggestion": "1",
            "group_id": group_id,
            "is_sug": "1",
        },
    )
    ok, data, err = _parse_collaboration_response(resp)
    if not ok:
        return False, [], err
    accounts = [item for item in (data or []) if item.get("is_exist") == 1]
    return True, accounts, ""


def _find_unique_account(
    accounts: list[dict[str, Any]],
    keyword: str,
) -> tuple[dict[str, Any] | None, str]:
    """从搜索结果中确定唯一账号.

    Returns:
        (account, error_message)。account 为 None 时表示未找到或不唯一。
    """
    if not accounts:
        return None, f"未找到与 '{keyword}' 匹配的可协同账号。仅支持讲师、学习负责人、子管理员、管理员角色。"
    if len(accounts) > 1:
        previews = [
            {
                "id": acc.get("id"),
                "user_name": acc.get("user_name"),
                "email": acc.get("email"),
                "phone": acc.get("phone"),
                "account": acc.get("account"),
            }
            for acc in accounts
        ]
        return None, f"找到多个匹配账号，请提供更精确的信息：{previews}"
    return accounts[0], ""


# ---------------------------------------------------------------------------
# Tools: 认证
# ---------------------------------------------------------------------------


mcp.tool()(
    make_login_tool(
        _TEACHER_SESSION_CONFIG,
        get_client=_get_client,
        get_session_manager=lambda: _session_manager,
        ok=_ok,
        err=_err,
    )
)


mcp.tool()(
    make_check_auth_tool(
        _TEACHER_SESSION_CONFIG,
        get_client=_get_client,
        ok=_ok,
        err=_err,
    )
)


# ---------------------------------------------------------------------------
# Tools: 会话管理
# ---------------------------------------------------------------------------


mcp.tool()(
    make_create_session_tool(
        _TEACHER_SESSION_CONFIG,
        get_session_manager=lambda: _session_manager,
        ok=_ok,
        err=_err,
    )
)


mcp.tool()(
    make_list_sessions_tool(
        _TEACHER_SESSION_CONFIG,
        get_session_manager=lambda: _session_manager,
        ok=_ok,
        err=_err,
    )
)


mcp.tool()(
    make_destroy_session_tool(
        _TEACHER_SESSION_CONFIG,
        get_session_manager=lambda: _session_manager,
        ok=_ok,
        err=_err,
    )
)


# ---------------------------------------------------------------------------
# Tools: 资源管理
# ---------------------------------------------------------------------------

@mcp.tool()
async def tch_upload_scorm(
    file_path: Annotated[
        str,
        Field(description="本地 SCORM zip 文件的绝对路径，如 /path/to/course.zip"),
    ],
    name: Annotated[
        str | None,
        Field(
            default=None,
            description="上传后在 UMU 资源库中显示的名称。如果不提供，默认使用原文件名（不含 .zip 后缀）。",
        ),
    ] = None,
    auto_rename: Annotated[
        bool,
        Field(
            default=False,
            description="上传成功后是否自动重命名。如果为 true 且提供了 name，会在上传后自动重命名资源。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """上传 SCORM 格式的课程数据包（.zip）到 UMU 资源库.

    触发条件：当讲师需要上传 SCORM 课程包到"我的音视频"资源库时调用。
    前置依赖：需先调用 tch_login 完成登录（讲师账号）。
    副作用：会在 UMU 资源库中创建新的 SCORM 资源条目。

    完整流程：
    1. 验证文件（路径安全检查、格式、大小）
    2. 获取腾讯云 COS 临时上传凭证
    3. 直传/分片上传到 COS（支持并发、重试、流式读取）
    4. 记录上传日志
    5. 注册为 SCORM 课程包
    6. 轮询解析处理状态（最多 120 秒）

    返回：包含 resource_id, file_url, scorm_url, task_id, status, progress 的 JSON
    """
    client = _get_client(session_id)

    try:
        # 路径安全检查（在 ScormUploader 内部也会执行，这里先提前检查给出更友好的错误）
        validate_file_path(file_path)
    except FileNotFoundError as e:
        return _err(
            error_code="FILE_NOT_FOUND",
            error_message=str(e),
            suggested_action="请提供正确的 zip 文件绝对路径",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_FILE",
            error_message=str(e),
            suggested_action="请提供有效的 SCORM 格式 zip 文件",
        )

    try:
        uploader = ScormUploader(client, client.base_url)
        result: UploadResult = await uploader.run(file_path, name)

        # 自动重命名（如果启用且提供了名称）
        rename_ok = False
        if auto_rename and name and result.resource_id:
            try:
                rename_resp = client.post(
                    client.desktop_url("/ajax/resource/renameresource"),
                    data={
                        "resource_id": result.resource_id,
                        "file_name": name,
                        "media_type": "videoweike",
                    },
                )
                if rename_resp.get("status") is True or rename_resp.get("error_code") == 0:
                    rename_ok = True
                    logger.info("自动重命名成功: %s", name)
                else:
                    logger.warning("自动重命名失败: %s", rename_resp.get("error", ""))
            except Exception as e:
                logger.warning("自动重命名异常: %s", e)

        return _ok(
            data={
                "resource_id": result.resource_id,
                "file_url": result.file_url,
                "scorm_url": result.scorm_url,
                "task_id": result.task_id,
                "status": result.status,
                "name": result.name,
                "file_size": result.file_size,
                "task_result": result.task_result,
                "progress": {
                    "stage": result.progress.stage,
                    "current_part": result.progress.current_part,
                    "total_parts": result.progress.total_parts,
                    "bytes_uploaded": result.progress.bytes_uploaded,
                    "bytes_total": result.progress.bytes_total,
                    "percent": result.progress.percent,
                    "estimated_seconds_remaining": result.progress.estimated_seconds_remaining,
                },
                "rename_status": "success" if rename_ok else "skipped",
            },
            next_action="proceed",
            suggested_action="资源已上传成功。如需修改名称，可调用 tch_rename_resource；如需确认，可调用 tch_list_resources",
        )

    except RuntimeError as e:
        logger.error("SCORM 上传失败: %s", e)
        return _err(
            error_code="SCORM_UPLOAD_ERROR",
            error_message=str(e),
            suggested_action="请检查文件路径、网络连接后重试",
        )
    except Exception as e:
        logger.exception("SCORM 上传异常")
        return _err(
            error_code="SCORM_UPLOAD_ERROR",
            error_message=str(e),
            suggested_action="请检查文件路径和网络连接后重试",
        )


@mcp.tool()
async def tch_list_resources(
    page: Annotated[int, Field(default=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Field(default=15, description="每页数量，默认 15")] = 15,
    search_keyword: Annotated[
        str | None,
        Field(default=None, description="搜索关键词，按文件名模糊匹配"),
    ] = None,
    media_type: Annotated[
        str,
        Field(default="videoweike", description="媒体类型筛选，默认 videoweike（音视频/SCORM）"),
    ] = "videoweike",
    ext_type: Annotated[
        str | None,
        Field(default=None, description="扩展类型筛选，如 'scorm' 只显示 SCORM 资源"),
    ] = None,
    fetch_all: Annotated[
        bool,
        Field(default=False, description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。"),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """查询讲师的音视频/SCORM 资源列表."""
    client = _get_client(session_id)

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        params = {
            "page": str(p),
            "is_recycle": "0",
            "search_keyword": search_keyword or "",
            "page_rows": str(sz),
            "order_by": "create_time",
            "is_desc": "1",
            "media_type": media_type,
            "status_str": "in_use,transcoding,wait_transcoding",
        }

        if ext_type:
            params["ext_type"] = ext_type

        resp = client.get(
            client.desktop_url("/ajax/resource/getresourcelist"),
            params=params,
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取资源列表失败"))

        data = resp.get("data", {})
        page_info = data.get("page_info", {})
        resource_list = data.get("list", [])

        formatted_list = []
        for item in resource_list:
            formatted_list.append({
                "id": item.get("id", ""),
                "name": item.get("file_name", ""),
                "size": int(item.get("file_size", 0) or 0),
                "url": item.get("url", ""),
                "ext": item.get("ext", ""),
                "media_type": item.get("media_type", ""),
                "transcoding_url": item.get("transcoding_url", ""),
                "transcoding_ext": item.get("transcoding_ext", ""),
                "create_time": item.get("create_time", ""),
                "status": item.get("status", ""),
            })

        total_all = int(page_info.get("list_total_num", 0) or 0)
        return formatted_list, total_all

    try:
        if fetch_all:
            batch_size = 50
            all_items: list[dict[str, Any]] = []
            total_all = 0
            current_page = 1

            while True:
                page_items, total_all = _fetch_page(current_page, batch_size)
                all_items.extend(page_items)

                report_pagination_progress(
                    "tch_list_resources",
                    current_page,
                    len(all_items),
                    total_all,
                    batch_size,
                )

                if not page_items or len(all_items) >= total_all:
                    report_pagination_progress(
                        "tch_list_resources",
                        current_page,
                        len(all_items),
                        total_all,
                        batch_size,
                        is_complete=True,
                    )
                    break

                if current_page >= 50:
                    report_pagination_progress(
                        "tch_list_resources",
                        current_page,
                        len(all_items),
                        total_all,
                        batch_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

                current_page += 1

            return _ok(
                data={
                    "resources": all_items,
                    "pagination": {
                        "total_all": total_all,
                        "current_page": current_page,
                        "page_size": batch_size,
                    },
                },
                next_action="proceed",
                suggested_action="如需上传新资源，调用 tch_upload_scorm；如需重命名，调用 tch_rename_resource",
            )

        # Single-page mode (original behavior)
        formatted_list, total_all = _fetch_page(page, page_size)

        return _ok(
            data={
                "resources": formatted_list,
                "pagination": {
                    "total": total_all,
                    "total_pages": 0,
                    "current_page": page,
                    "page_size": page_size,
                },
            },
            next_action="proceed",
            suggested_action="如需上传新资源，调用 tch_upload_scorm；如需重命名，调用 tch_rename_resource",
        )

    except Exception as e:
        logger.exception("查询资源列表失败")
        return _err(
            error_code="LIST_RESOURCES_FAILED",
            error_message=str(e),
            suggested_action="请检查网络连接后重试",
        )


@mcp.tool()
async def tch_rename_resource(
    resource_id: Annotated[str, Field(description="资源 ID，可从 tch_list_resources 或 tch_upload_scorm 返回结果中获取")],
    file_name: Annotated[str, Field(description="新的文件名（不需要包含 .zip 后缀，系统会自动保留）")],
    media_type: Annotated[
        str,
        Field(default="videoweike", description="媒体类型，默认 videoweike"),
    ] = "videoweike",
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """重命名资源库中的已有资源."""
    client = _get_client(session_id)

    try:
        resp = client.post(
            client.desktop_url("/ajax/resource/renameresource"),
            data={
                "resource_id": resource_id,
                "file_name": file_name,
                "media_type": media_type,
            },
        )

        if resp.get("status") is True or resp.get("error_code") == 0:
            return _ok(
                data={
                    "resource_id": resource_id,
                    "new_name": file_name,
                    "result": resp.get("data", {}).get("result", 1),
                },
                next_action="proceed",
                suggested_action="重命名成功。可调用 tch_list_resources 确认",
            )
        else:
            return _err(
                error_code="RENAME_FAILED",
                error_message=resp.get("error", "重命名失败"),
                suggested_action="请检查 resource_id 是否正确",
            )

    except Exception as e:
        logger.exception("重命名资源失败")
        return _err(
            error_code="RENAME_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和资源 ID 后重试",
        )


@mcp.tool()
async def tch_delete_resource(
    resource_id: Annotated[str, Field(description="资源 ID，可从 tch_list_resources 获取")],
    media_type: Annotated[
        str,
        Field(default="videoweike", description="媒体类型，默认 videoweike"),
    ] = "videoweike",
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """删除资源库中的资源（移到回收站）.

    触发条件：当需要删除已上传的资源时调用。
    前置依赖：需先调用 tch_login 完成登录（讲师账号）。
    副作用：资源会被移到回收站，可在 UMU 后台彻底删除或恢复。

    返回：操作结果 JSON
    """
    client = _get_client(session_id)

    try:
        resp = client.post(
            client.desktop_url("/ajax/resource/deleteresource"),
            data={
                "resource_id": resource_id,
                "media_type": media_type,
            },
        )

        if resp.get("status") is True or resp.get("error_code") == 0:
            return _ok(
                data={"resource_id": resource_id, "deleted": True},
                next_action="proceed",
                suggested_action="资源已删除（移到回收站）。如需彻底删除，请登录 UMU 后台",
            )
        else:
            return _err(
                error_code="DELETE_FAILED",
                error_message=resp.get("error", "删除失败"),
                suggested_action="请检查 resource_id 是否正确，或权限是否充足",
            )

    except Exception as e:
        logger.exception("删除资源失败")
        return _err(
            error_code="DELETE_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和资源 ID 后重试",
        )


# ---------------------------------------------------------------------------
# Tools: 文档管理（我的文档）
# ---------------------------------------------------------------------------

@mcp.tool()
async def tch_upload_document(
    file_path: Annotated[
        str,
        Field(description="本地文档文件的绝对路径，支持 .xlsx/.xls, .docx/.doc, .pptx/.ppt, .pdf, .txt"),
    ],
    name: Annotated[
        str | None,
        Field(
            default=None,
            description="上传后在 UMU 文档库中显示的名称。如果不提供，默认使用原文件名。",
        ),
    ] = None,
    skip_existing: Annotated[
        bool,
        Field(
            default=False,
            description="如果为 True，上传前检查是否已有同名同大小的文档，存在则跳过上传并返回已有资源 ID。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """上传文档（Excel/Word/PPT/PDF）到"我的文档"资源库.

    触发条件：当讲师需要上传文档到"课程资源 > 我的文档"时调用。
    前置依赖：需先调用 tch_login 完成登录（讲师账号）。
    副作用：会在 UMU 文档资源库中创建新的文档条目。

    完整流程：
    1. 验证文件（路径安全检查、格式、大小）
    2. （可选）幂等性检查：根据文件名+大小查重
    3. 获取腾讯云 COS 临时上传凭证
    4. 直传/分片上传到 COS（支持并发、重试、流式读取）
       - 小于 50MB：直接 PUT 上传
       - 大于等于 50MB：分片并发上传
    5. 记录上传日志
    6. resourceCallback 注册到文档列表
    7. 防御性验证：确认资源在列表中

    支持的格式：
    - Excel: .xlsx, .xls
    - Word: .docx, .doc
    - PowerPoint: .pptx, .ppt
    - PDF: .pdf

    返回：包含 resource_id, file_url, name, file_size, progress, next_actions 的 JSON
    """
    client = _get_client(session_id)

    try:
        validate_document_path(file_path)
    except FileNotFoundError as e:
        return _err(
            error_code="FILE_NOT_FOUND",
            error_message=str(e),
            suggested_action="请提供正确的文档文件绝对路径",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_FILE",
            error_message=str(e),
            suggested_action="请提供支持的文档格式：.xlsx, .xls, .docx, .doc, .pptx, .ppt, .pdf, .txt",
        )

    file_size = os.path.getsize(file_path)
    display_name = name or os.path.basename(file_path)

    # 幂等性检查：根据文件名+大小查重
    if skip_existing:
        existing_id = _find_document_by_name_size(client, display_name, file_size)
        if existing_id:
            logger.info("文档已存在，跳过上传: %s (resource_id=%s)", display_name, existing_id)
            return _ok(
                data={
                    "resource_id": existing_id,
                    "name": display_name,
                    "file_size": file_size,
                    "status": "skipped",
                    "is_duplicate": True,
                },
                next_action="proceed",
                suggested_action=f"文档已存在，跳过上传。resource_id={existing_id}。如需删除旧版本，调用 tch_delete_document(resource_id='{existing_id}')",
            )

    try:
        uploader = DocumentUploader(client, client.base_url)
        result: UploadResult = await uploader.run(file_path, name)

        # 防御性验证：确认资源已注册到列表
        if result.resource_id:
            is_verified = _verify_resource_registered(client, result.resource_id)
            if not is_verified:
                logger.warning(
                    "文档上传成功但资源注册验证未通过: resource_id=%s",
                    result.resource_id,
                )

        # 构建 next_actions 命令模板
        next_actions = []
        if result.resource_id:
            next_actions.append(
                f"tch_rename_document(resource_id='{result.resource_id}', file_name='新名称')"
            )
            next_actions.append(
                f"tch_delete_document(resource_id='{result.resource_id}')"
            )

        return _ok(
            data={
                "resource_id": result.resource_id,
                "file_url": result.file_url,
                "name": result.name,
                "file_size": result.file_size,
                "status": result.status,
                "is_verified": is_verified if result.resource_id else False,
                "progress": {
                    "stage": result.progress.stage,
                    "current_part": result.progress.current_part,
                    "total_parts": result.progress.total_parts,
                    "bytes_uploaded": result.progress.bytes_uploaded,
                    "bytes_total": result.progress.bytes_total,
                    "percent": result.progress.percent,
                    "estimated_seconds_remaining": result.progress.estimated_seconds_remaining,
                },
                "next_actions": next_actions,
            },
            next_action="proceed",
            suggested_action="文档上传成功。可调用 tch_list_documents 查看列表，或使用 next_actions 中的命令进行后续操作",
        )

    except RuntimeError as e:
        logger.error("文档上传失败: %s", e)
        return _err(
            error_code="DOCUMENT_UPLOAD_ERROR",
            error_message=str(e),
            suggested_action="请检查文件路径、网络连接后重试",
        )
    except Exception as e:
        logger.exception("文档上传异常")
        return _err(
            error_code="DOCUMENT_UPLOAD_ERROR",
            error_message=str(e),
            suggested_action="请检查文件路径和网络连接后重试",
        )


@mcp.tool()
async def tch_list_documents(
    page: Annotated[int, Field(default=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Field(default=15, description="每页数量，默认 15")] = 15,
    search_keyword: Annotated[
        str | None,
        Field(default=None, description="搜索关键词，按文件名模糊匹配"),
    ] = None,
    fetch_all: Annotated[
        bool,
        Field(default=False, description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。"),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """查询讲师"我的文档"中的文档列表.

    触发条件：需要查看已上传的文档列表、查找文档 ID 用于后续操作。
    前置依赖：需先调用 tch_login 完成登录（讲师账号）。

    返回每个文档的结构化摘要：
    - id: 文档资源 ID
    - name: 文件名
    - size: 文件大小（字节）
    - url: 文件 URL
    - ext: 文件扩展名
    - create_time: 创建时间
    - status: 状态

    返回：文档列表 JSON（含分页信息）
    """
    client = _get_client(session_id)

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        params = {
            "page": str(p),
            "is_recycle": "0",
            "search_keyword": search_keyword or "",
            "page_rows": str(sz),
            "order_by": "create_time",
            "is_desc": "1",
            "media_type": "docweike",
            "status_str": "in_use,transcoding,wait_transcoding",
        }

        resp = client.get(
            client.desktop_url("/ajax/resource/getresourcelist"),
            params=params,
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取文档列表失败"))

        data = resp.get("data", {})
        page_info = data.get("page_info", {})
        resource_list = data.get("list", [])

        formatted_list = []
        zero_size_count = 0
        for item in resource_list:
            file_size = int(item.get("file_size", 0) or 0)
            status = item.get("status", "")
            # P3-9: size=0 特殊标记（可能是 UMU 后端缓存问题）
            size_note = None
            if file_size == 0 and status != "wait_transcoding":
                zero_size_count += 1
                size_note = "size_unknown"
            formatted_list.append({
                "id": item.get("id", ""),
                "name": item.get("file_name", ""),
                "size": file_size,
                "size_formatted": _format_size(file_size),
                "size_note": size_note,
                "url": item.get("url", ""),
                "ext": item.get("ext", ""),
                "media_type": item.get("media_type", ""),
                "create_time": item.get("create_time", ""),
                "status": status,
            })

        total_all = int(page_info.get("list_total_num", 0) or 0)
        return formatted_list, total_all

    try:
        if fetch_all:
            batch_size = 50
            all_items: list[dict[str, Any]] = []
            total_all = 0
            current_page = 1

            while True:
                page_items, total_all = _fetch_page(current_page, batch_size)
                all_items.extend(page_items)

                report_pagination_progress(
                    "tch_list_documents",
                    current_page,
                    len(all_items),
                    total_all,
                    batch_size,
                )

                if not page_items or len(all_items) >= total_all:
                    report_pagination_progress(
                        "tch_list_documents",
                        current_page,
                        len(all_items),
                        total_all,
                        batch_size,
                        is_complete=True,
                    )
                    break

                if current_page >= 50:
                    report_pagination_progress(
                        "tch_list_documents",
                        current_page,
                        len(all_items),
                        total_all,
                        batch_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

                current_page += 1

            # P3-9: size=0 提示
            zero_size_count = sum(1 for item in all_items if item.get("size_note") == "size_unknown")
            size_hint = ""
            if zero_size_count > 0:
                size_hint = f" 注意：有 {zero_size_count} 个文档大小为 0（可能是 UMU 后端数据未同步），不影响使用。"

            return _ok(
                data={
                    "documents": all_items,
                    "pagination": {
                        "total_all": total_all,
                        "current_page": current_page,
                        "page_size": batch_size,
                    },
                },
                next_action="proceed",
                suggested_action=f"如需上传新文档，调用 tch_upload_document；如需批量上传，调用 tch_upload_documents_batch；如需重命名，调用 tch_rename_document；如需删除，调用 tch_delete_document 或 tch_delete_documents_batch。{size_hint}",
            )

        # Single-page mode (original behavior)
        formatted_list, _ = _fetch_page(page, page_size)

        # P3-9: size=0 提示
        zero_size_count = sum(1 for item in formatted_list if item.get("size_note") == "size_unknown")
        size_hint = ""
        if zero_size_count > 0:
            size_hint = f" 注意：有 {zero_size_count} 个文档大小为 0（可能是 UMU 后端数据未同步），不影响使用。"

        return _ok(
            data={
                "documents": formatted_list,
                "pagination": {
                    "total": 0,
                    "total_pages": 0,
                    "current_page": page,
                    "page_size": page_size,
                },
            },
            next_action="proceed",
            suggested_action=f"如需上传新文档，调用 tch_upload_document；如需批量上传，调用 tch_upload_documents_batch；如需重命名，调用 tch_rename_document；如需删除，调用 tch_delete_document 或 tch_delete_documents_batch。{size_hint}",
        )

    except Exception as e:
        logger.exception("查询文档列表失败")
        return _err(
            error_code="LIST_DOCUMENTS_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接后重试",
        )


@mcp.tool()
async def tch_rename_document(
    resource_id: Annotated[str, Field(description="文档资源 ID，可从 tch_list_documents 获取")],
    file_name: Annotated[str, Field(description="新的文件名（不需要包含扩展名，系统会自动保留）")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """重命名"我的文档"中的已有文档.

    触发条件：当需要修改已上传文档的显示名称时调用。
    前置依赖：需先调用 tch_login 完成登录，并已有文档 resource_id。

    返回：操作结果 JSON
    """
    client = _get_client(session_id)

    try:
        resp = client.post(
            client.desktop_url("/ajax/resource/renameresource"),
            data={
                "resource_id": resource_id,
                "file_name": file_name,
                "media_type": "docweike",
            },
        )

        if resp.get("status") is True or resp.get("error_code") == 0:
            return _ok(
                data={
                    "resource_id": resource_id,
                    "new_name": file_name,
                    "result": resp.get("data", {}).get("result", 1),
                },
                next_action="proceed",
                suggested_action="重命名成功。可调用 tch_list_documents 确认",
            )
        else:
            return _err(
                error_code="RENAME_DOCUMENT_FAILED",
                error_message=resp.get("error", "重命名失败"),
                suggested_action="请检查 resource_id 是否正确",
            )

    except Exception as e:
        logger.exception("重命名文档失败")
        return _err(
            error_code="RENAME_DOCUMENT_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和资源 ID 后重试",
        )


@mcp.tool()
async def tch_delete_document(
    resource_id: Annotated[str, Field(description="文档资源 ID，可从 tch_list_documents 获取")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """删除"我的文档"中的文档（移到回收站）.

    触发条件：当需要删除已上传的文档时调用。
    前置依赖：需先调用 tch_login 完成登录（讲师账号）。
    副作用：文档会被移到回收站，可在 UMU 后台彻底删除或恢复。

    删除前会自动检查文档是否被课程小节引用，如果已被引用会返回警告。

    返回：操作结果 JSON
    """
    client = _get_client(session_id)

    try:
        # 1. 检查是否被课程引用
        try:
            refer_resp = client.get(
                client.desktop_url("/ajax/resource/isreferredbysession"),
                params={
                    "resource_id": resource_id,
                    "media_type": "docweike",
                },
            )
            is_referred = refer_resp.get("data", {}).get("is_referred", False)
            if is_referred:
                logger.warning("文档 %s 被课程小节引用，删除可能影响课程内容", resource_id)
        except Exception as e:
            logger.warning("检查文档引用状态失败（非致命）: %s", e)
            is_referred = False

        # 2. 删除文档
        resp = client.post(
            client.desktop_url("/ajax/resource/deleteresource"),
            data={
                "resource_id": resource_id,
                "delete_mode": "1",
                "media_type": "docweike",
            },
        )

        if resp.get("status") is True or resp.get("error_code") == 0:
            if is_referred:
                # 增强警告：被引用的资源删除可能影响课程内容
                return _ok(
                    data={
                        "resource_id": resource_id,
                        "deleted": True,
                        "was_referred": True,
                        "warning_level": "HIGH",
                    },
                    next_action="confirm",
                    suggested_action="⚠️ 此文档已被课程小节引用，删除后相关课程将无法正常访问该文档。如需恢复，请登录 UMU 后台的回收站。如需确认继续，可忽略此警告。",
                )
            return _ok(
                data={
                    "resource_id": resource_id,
                    "deleted": True,
                    "was_referred": False,
                },
                next_action="proceed",
                suggested_action="文档已删除（移到回收站）。如需彻底删除，请登录 UMU 后台",
            )
        else:
            return _err(
                error_code="DELETE_DOCUMENT_FAILED",
                error_message=resp.get("error", "删除失败"),
                suggested_action="请检查 resource_id 是否正确，或权限是否充足",
            )

    except Exception as e:
        logger.exception("删除文档失败")
        return _err(
            error_code="DELETE_DOCUMENT_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和资源 ID 后重试",
        )


@mcp.tool()
async def tch_delete_documents_batch(
    resource_ids: Annotated[
        list[str],
        Field(description="要删除的文档资源 ID 列表，可从 tch_list_documents 获取"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """批量删除"我的文档"中的文档（移到回收站）.

    触发条件：当需要一次性删除多个文档时调用。
    前置依赖：需先调用 tch_login 完成登录（讲师账号）。

    每个文档删除前会自动检查是否被课程小节引用，已被引用的会单独返回警告。

    返回：批量操作结果 JSON（含成功/失败列表和引用警告列表）
    """
    client = _get_client(session_id)

    if not resource_ids:
        return _err(
            error_code="EMPTY_RESOURCE_IDS",
            error_message="resource_ids 不能为空列表",
            suggested_action="请提供至少一个 resource_id",
        )

    success_ids: list[str] = []
    failed_ids: list[dict] = []
    warned_ids: list[dict] = []

    for i, rid in enumerate(resource_ids, 1):
        logger.info("[%d/%d] 删除文档 %s", i, len(resource_ids), rid)
        try:
            # 检查引用
            try:
                refer_resp = client.get(
                    client.desktop_url("/ajax/resource/isreferredbysession"),
                    params={"resource_id": rid, "media_type": "docweike"},
                )
                is_referred = refer_resp.get("data", {}).get("is_referred", False)
            except Exception:
                is_referred = False

            # 删除
            resp = client.post(
                client.desktop_url("/ajax/resource/deleteresource"),
                data={
                    "resource_id": rid,
                    "delete_mode": "1",
                    "media_type": "docweike",
                },
            )

            if resp.get("status") is True or resp.get("error_code") == 0:
                if is_referred:
                    warned_ids.append({"resource_id": rid, "was_referred": True})
                else:
                    success_ids.append(rid)
            else:
                failed_ids.append({
                    "resource_id": rid,
                    "error": resp.get("error", "删除失败"),
                })
        except Exception as e:
            failed_ids.append({"resource_id": rid, "error": str(e)})

    return _ok(
        data={
            "summary": {
                "total": len(resource_ids),
                "success": len(success_ids),
                "warned": len(warned_ids),
                "failed": len(failed_ids),
            },
            "success_ids": success_ids,
            "warned_ids": warned_ids,
            "failed_ids": failed_ids,
        },
        next_action="proceed",
        suggested_action=f"批量删除完成：成功 {len(success_ids)} 个，警告（被引用） {len(warned_ids)} 个，失败 {len(failed_ids)} 个。如需彻底删除回收站中的文档，请登录 UMU 后台。",
    )


@mcp.tool()
async def tch_upload_documents_batch(
    file_paths: Annotated[
        list[str],
        Field(description="本地文档文件的绝对路径列表，支持 .xlsx/.xls, .docx/.doc, .pptx/.ppt, .pdf, .txt"),
    ],
    skip_existing: Annotated[
        bool,
        Field(
            default=False,
            description="如果为 True，每个文件上传前检查是否已有同名同大小的文档，存在则跳过。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """批量上传文档到"我的文档"资源库.

    触发条件：当讲师需要一次性上传多个文档时调用。
    前置依赖：需先调用 tch_login 完成登录（讲师账号）。

    上传前会先验证每个文件路径的有效性，不存在的文件会报告错误但不会中断其他文件。
    支持幂等性保护（skip_existing=True）。

    返回：批量操作结果 JSON（含成功/跳过/失败列表）
    """
    client = _get_client(session_id)

    if not file_paths:
        return _err(
            error_code="EMPTY_FILE_PATHS",
            error_message="file_paths 不能为空列表",
            suggested_action="请提供至少一个文档文件路径",
        )

    # 前置路径校验
    invalid_paths: list[dict] = []
    valid_paths: list[str] = []
    for fp in file_paths:
        if not os.path.isfile(fp):
            # 尝试查找类似的文件（处理空格差异）
            suggestions = []
            dir_path = os.path.dirname(fp) or "."
            base_name = os.path.basename(fp)
            try:
                for fname in os.listdir(dir_path):
                    if fname.replace(" ", "") == base_name.replace(" ", ""):
                        suggestions.append(os.path.join(dir_path, fname))
            except OSError:
                pass
            invalid_paths.append({
                "path": fp,
                "reason": "文件不存在",
                "suggestions": suggestions,
            })
        else:
            try:
                validate_document_path(fp)
                valid_paths.append(fp)
            except (FileNotFoundError, ValueError) as e:
                invalid_paths.append({
                    "path": fp,
                    "reason": str(e),
                    "suggestions": [],
                })

    if not valid_paths:
        return _err(
            error_code="ALL_FILES_INVALID",
            error_message=f"所有文件路径均无效。{invalid_paths[0]['reason'] if invalid_paths else ''}",
            suggested_action="请检查文件路径是否正确。可能的原因：文件名包含隐藏空格、文件已移动、格式不支持。",
        )

    success_results: list[dict] = []
    skipped_results: list[dict] = []
    failed_results: list[dict] = []

    for i, fp in enumerate(valid_paths, 1):
        file_size = os.path.getsize(fp)
        display_name = os.path.basename(fp)
        logger.info("[%d/%d] 上传文档: %s", i, len(valid_paths), display_name)

        # 幂等性检查
        if skip_existing:
            existing_id = _find_document_by_name_size(client, display_name, file_size)
            if existing_id:
                logger.info("  跳过（已存在）: %s", display_name)
                skipped_results.append({
                    "path": fp,
                    "name": display_name,
                    "resource_id": existing_id,
                })
                continue

        try:
            uploader = DocumentUploader(client, client.base_url)
            result: UploadResult = await uploader.run(fp)

            # 防御性验证
            is_verified = False
            if result.resource_id:
                is_verified = _verify_resource_registered(client, result.resource_id)

            success_results.append({
                "path": fp,
                "resource_id": result.resource_id,
                "name": result.name,
                "file_size": result.file_size,
                "is_verified": is_verified,
            })
        except Exception as e:
            failed_results.append({"path": fp, "error": str(e)})

    return _ok(
        data={
            "summary": {
                "total": len(file_paths),
                "valid": len(valid_paths),
                "invalid": len(invalid_paths),
                "success": len(success_results),
                "skipped": len(skipped_results),
                "failed": len(failed_results),
            },
            "invalid_paths": invalid_paths,
            "success_results": success_results,
            "skipped_results": skipped_results,
            "failed_results": failed_results,
        },
        next_action="proceed",
        suggested_action=f"批量上传完成：成功 {len(success_results)} 个，跳过 {len(skipped_results)} 个，失败 {len(failed_results)} 个，无效路径 {len(invalid_paths)} 个。可调用 tch_list_documents 查看完整列表。",
    )


# ---------------------------------------------------------------------------
# Tools: 音视频管理（我的音视频）
# ---------------------------------------------------------------------------

@mcp.tool()
async def tch_upload_audio_video(
    file_path: Annotated[
        str,
        Field(description="本地音视频文件的绝对路径，支持 mp4, mov, avi, mkv, mp3, wav, flac 等 36 种格式"),
    ],
    name: Annotated[
        str | None,
        Field(
            default=None,
            description="上传后在 UMU 音视频库中显示的名称。如果不提供，默认使用原文件名。",
        ),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """上传音视频文件到"我的音视频"资源库.

    触发条件：当讲师需要上传音视频到"课程资源 > 我的音视频"时调用。
    前置依赖：需先调用 tch_login 完成登录（讲师账号）。
    副作用：会在 UMU 音视频资源库中创建新的音视频条目。

    完整流程：
    1. 验证文件（路径安全检查、格式、大小）
    2. 获取腾讯云 COS 临时上传凭证
    3. 直传/分片上传到 COS（支持并发、重试、流式读取）
       - 小于 50MB：直接 PUT 上传
       - 大于等于 50MB：分片并发上传
    4. 记录上传日志
    5. resourceCallback 注册到音视频列表

    支持的格式（36 种）：
    - 视频：3gp, 3gpp, avi, flv, f4v, mkv, mov, mp4, m4a, mpeg, mpg, ts, mts,
      wmv, rm, rmvb, webm, dv, m2v, m4v, ogv, 3g2
    - 音频：mp3, mp1, mp2, aac, ac3, flac, au, 3ga, amr, wav, wma, ra, ogg, dsf

    文件大小限制：1024MB（1GB）

    返回：包含 resource_id, file_url, name, file_size, progress, next_actions 的 JSON
    """
    client = _get_client(session_id)

    try:
        validate_video_path(file_path)
    except FileNotFoundError as e:
        return _err(
            error_code="FILE_NOT_FOUND",
            error_message=str(e),
            suggested_action="请提供正确的音视频文件绝对路径",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_FILE",
            error_message=str(e),
            suggested_action="请提供支持的音视频格式，文件大小须在 1024MB 以内",
        )

    try:
        uploader = VideoUploader(client, client.base_url)
        result: UploadResult = await uploader.run(file_path, name)

        # 构建 next_actions 命令模板
        next_actions = []
        if result.resource_id:
            next_actions.append(
                f"tch_rename_audio_video(resource_id='{result.resource_id}', file_name='新名称')"
            )
            next_actions.append(
                f"tch_delete_audio_video(resource_id='{result.resource_id}')"
            )

        return _ok(
            data={
                "resource_id": result.resource_id,
                "file_url": result.file_url,
                "name": result.name,
                "file_size": result.file_size,
                "status": result.status,
                "progress": {
                    "stage": result.progress.stage,
                    "current_part": result.progress.current_part,
                    "total_parts": result.progress.total_parts,
                    "bytes_uploaded": result.progress.bytes_uploaded,
                    "bytes_total": result.progress.bytes_total,
                    "percent": result.progress.percent,
                    "estimated_seconds_remaining": result.progress.estimated_seconds_remaining,
                },
                "next_actions": next_actions,
            },
            next_action="proceed",
            suggested_action="音视频上传成功。可调用 tch_list_audio_videos 查看列表，或使用 next_actions 中的命令进行后续操作",
        )

    except RuntimeError as e:
        logger.error("音视频上传失败: %s", e)
        return _err(
            error_code="VIDEO_UPLOAD_ERROR",
            error_message=str(e),
            suggested_action="请检查文件路径、网络连接后重试",
        )
    except Exception as e:
        logger.exception("音视频上传异常")
        return _err(
            error_code="VIDEO_UPLOAD_ERROR",
            error_message=str(e),
            suggested_action="请检查文件路径和网络连接后重试",
        )


@mcp.tool()
async def tch_list_audio_videos(
    page: Annotated[int, Field(default=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Field(default=15, description="每页数量，默认 15")] = 15,
    search_keyword: Annotated[
        str | None,
        Field(default=None, description="搜索关键词，按文件名模糊匹配"),
    ] = None,
    fetch_all: Annotated[
        bool,
        Field(default=False, description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。"),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """查询讲师"我的音视频"中的音视频列表.

    触发条件：需要查看已上传的音视频列表、查找音视频 ID 用于后续操作。
    前置依赖：需先调用 tch_login 完成登录（讲师账号）。

    返回每个音视频的结构化摘要：
    - id: 音视频资源 ID
    - name: 文件名
    - size: 文件大小（字节）
    - url: 文件 URL
    - ext: 文件扩展名
    - create_time: 创建时间
    - status: 状态

    返回：音视频列表 JSON（含分页信息）
    """
    client = _get_client(session_id)

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        params = {
            "page": str(p),
            "is_recycle": "0",
            "search_keyword": search_keyword or "",
            "page_rows": str(sz),
            "order_by": "create_time",
            "is_desc": "1",
            "media_type": VIDEO_MEDIA_TYPE,
            "status_str": "in_use,transcoding,wait_transcoding",
        }

        resp = client.get(
            client.desktop_url("/ajax/resource/getresourcelist"),
            params=params,
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取音视频列表失败"))

        data = resp.get("data", {})
        page_info = data.get("page_info", {})
        resource_list = data.get("list", [])

        formatted_list = []
        for item in resource_list:
            file_size = int(item.get("file_size", 0) or 0)
            formatted_list.append({
                "id": item.get("id", ""),
                "name": item.get("file_name", ""),
                "size": file_size,
                "size_formatted": _format_size(file_size),
                "url": item.get("url", ""),
                "ext": item.get("ext", ""),
                "media_type": item.get("media_type", ""),
                "create_time": item.get("create_time", ""),
                "status": item.get("status", ""),
            })

        total_all = int(page_info.get("list_total_num", 0) or 0)
        return formatted_list, total_all

    try:
        if fetch_all:
            batch_size = 50
            all_items: list[dict[str, Any]] = []
            total_all = 0
            current_page = 1

            while True:
                page_items, total_all = _fetch_page(current_page, batch_size)
                all_items.extend(page_items)

                report_pagination_progress(
                    "tch_list_audio_videos",
                    current_page,
                    len(all_items),
                    total_all,
                    batch_size,
                )

                if not page_items or len(all_items) >= total_all:
                    report_pagination_progress(
                        "tch_list_audio_videos",
                        current_page,
                        len(all_items),
                        total_all,
                        batch_size,
                        is_complete=True,
                    )
                    break

                if current_page >= 50:
                    report_pagination_progress(
                        "tch_list_audio_videos",
                        current_page,
                        len(all_items),
                        total_all,
                        batch_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

                current_page += 1

            return _ok(
                data={
                    "videos": all_items,
                    "pagination": {
                        "total_all": total_all,
                        "current_page": current_page,
                        "page_size": batch_size,
                    },
                },
                next_action="proceed",
                suggested_action="如需上传新音视频，调用 tch_upload_audio_video；如需重命名，调用 tch_rename_audio_video；如需删除，调用 tch_delete_audio_video。",
            )

        # Single-page mode (original behavior)
        formatted_list, _ = _fetch_page(page, page_size)

        return _ok(
            data={
                "videos": formatted_list,
                "pagination": {
                    "total": 0,
                    "total_pages": 0,
                    "current_page": page,
                    "page_size": page_size,
                },
            },
            next_action="proceed",
            suggested_action="如需上传新音视频，调用 tch_upload_audio_video；如需重命名，调用 tch_rename_audio_video；如需删除，调用 tch_delete_audio_video。",
        )

    except Exception as e:
        logger.exception("查询音视频列表失败")
        return _err(
            error_code="LIST_VIDEOS_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接后重试",
        )


@mcp.tool()
async def tch_rename_audio_video(
    resource_id: Annotated[str, Field(description="音视频资源 ID，可从 tch_list_audio_videos 获取")],
    file_name: Annotated[str, Field(description="新的文件名（不需要包含扩展名，系统会自动保留）")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """重命名"我的音视频"中的已有音视频.

    触发条件：当需要修改已上传音视频的显示名称时调用。
    前置依赖：需先调用 tch_login 完成登录，并已有音视频 resource_id。

    返回：操作结果 JSON
    """
    client = _get_client(session_id)

    try:
        resp = client.post(
            client.desktop_url("/ajax/resource/renameresource"),
            data={
                "resource_id": resource_id,
                "file_name": file_name,
                "media_type": VIDEO_MEDIA_TYPE,
            },
        )

        if resp.get("status") is True or resp.get("error_code") == 0:
            return _ok(
                data={
                    "resource_id": resource_id,
                    "new_name": file_name,
                    "result": resp.get("data", {}).get("result", 1),
                },
                next_action="proceed",
                suggested_action="重命名成功。可调用 tch_list_audio_videos 确认",
            )
        else:
            return _err(
                error_code="RENAME_VIDEO_FAILED",
                error_message=resp.get("error", "重命名失败"),
                suggested_action="请检查 resource_id 是否正确",
            )

    except Exception as e:
        logger.exception("重命名音视频失败")
        return _err(
            error_code="RENAME_VIDEO_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和资源 ID 后重试",
        )


@mcp.tool()
async def tch_delete_audio_video(
    resource_id: Annotated[str, Field(description="音视频资源 ID，可从 tch_list_audio_videos 获取")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """删除"我的音视频"中的音视频（移到回收站）.

    触发条件：当需要删除已上传的音视频时调用。
    前置依赖：需先调用 tch_login 完成登录（讲师账号）。
    副作用：音视频会被移到回收站，可在 UMU 后台彻底删除或恢复。

    删除前会自动检查音视频是否被课程小节引用，如果已被引用会返回警告。

    返回：操作结果 JSON
    """
    client = _get_client(session_id)

    try:
        # 1. 检查是否被课程引用
        try:
            refer_resp = client.get(
                client.desktop_url("/ajax/resource/isreferredbysession"),
                params={
                    "resource_id": resource_id,
                    "media_type": VIDEO_MEDIA_TYPE,
                },
            )
            is_referred = refer_resp.get("data", {}).get("is_referred", False)
            if is_referred:
                logger.warning("音视频 %s 被课程小节引用，删除可能影响课程内容", resource_id)
        except Exception as e:
            logger.warning("检查音视频引用状态失败（非致命）: %s", e)
            is_referred = False

        # 2. 删除音视频
        resp = client.post(
            client.desktop_url("/ajax/resource/deleteresource"),
            data={
                "resource_id": resource_id,
                "delete_mode": "1",
                "media_type": VIDEO_MEDIA_TYPE,
            },
        )

        if resp.get("status") is True or resp.get("error_code") == 0:
            if is_referred:
                return _ok(
                    data={
                        "resource_id": resource_id,
                        "deleted": True,
                        "was_referred": True,
                        "warning_level": "HIGH",
                    },
                    next_action="confirm",
                    suggested_action="⚠️ 此音视频已被课程小节引用，删除后相关课程将无法正常访问该音视频。如需恢复，请登录 UMU 后台的回收站。如需确认继续，可忽略此警告。",
                )
            return _ok(
                data={
                    "resource_id": resource_id,
                    "deleted": True,
                    "was_referred": False,
                },
                next_action="proceed",
                suggested_action="音视频已删除（移到回收站）。如需彻底删除，请登录 UMU 后台",
            )
        else:
            return _err(
                error_code="DELETE_VIDEO_FAILED",
                error_message=resp.get("error", "删除失败"),
                suggested_action="请检查 resource_id 是否正确，或权限是否充足",
            )

    except Exception as e:
        logger.exception("删除音视频失败")
        return _err(
            error_code="DELETE_VIDEO_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和资源 ID 后重试",
        )


# ---------------------------------------------------------------------------
# Tools: 课程管理
# ---------------------------------------------------------------------------

@mcp.tool()
async def tch_create_course(
    title: Annotated[str, Field(description="课程标题，2-100 字符")],
    course_type: Annotated[
        int,
        Field(default=1, description="课程形式：1=在线课程，2=面授课程，3=混合课程"),
    ] = 1,
    category_ids: Annotated[
        list[str] | None,
        Field(default=None, description="课程分类 ID 列表，可选。与 category_names 二选一"),
    ] = None,
    category_names: Annotated[
        list[str] | None,
        Field(
            default=None,
            description='课程分类名称列表，支持完整路径如 ["课程系列 > 新能力系列 > 客户思维"]。'
                        "与 category_ids 同时提供时，category_names 优先。"
                        "可先调用 tch_get_categories 查看可用分类。",
        ),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Field(default=None, description="课程标签文本列表，可选"),
    ] = None,
    cover_image_path: Annotated[
        str | None,
        Field(default=None, description="本地封面图片路径（jpg/png），可选"),
    ] = None,
    bg_image_path: Annotated[
        str | None,
        Field(default=None, description="本地背景图片路径（jpg/png），可选"),
    ] = None,
    desc_plain: Annotated[
        str,
        Field(default="", description="纯文本课程介绍"),
    ] = "",
    desc_richtext: Annotated[
        str,
        Field(default="", description="富文本课程介绍（HTML 格式），可选"),
    ] = "",
    start_date: Annotated[
        str,
        Field(default="", description="课程起始日期，格式 YYYY-MM-DD，可选"),
    ] = "",
    start_time_str: Annotated[
        str,
        Field(default="", description="课程起始时间，格式 HH:MM，可选"),
    ] = "",
    end_time_str: Annotated[
        str,
        Field(default="", description="课程结束时间，格式 HH:MM，可选"),
    ] = "",
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """创建不含小节的空课程.

    触发条件：当讲师需要创建一门新课程时调用。
    前置依赖：需先调用 tch_login 完成登录（讲师账号）。

    内部流程：
    1. 如有封面图，上传封面到 COS 并获取 URL
    2. 如有背景图，上传背景到 COS 并获取 URL
    3. 如有富文本介绍，创建富文本内容并获取 multimedia_id
    4. 调用 e_saveGroup 保存课程
    5. 返回 group_id 等课程信息

    时间字段说明：
    - start_date + start_time_str + end_time_str 共同定义课程的标准学时
    - 例如：start_date="2026-06-01", start_time_str="09:00", end_time_str="09:30"
    - 表示课程在 2026-06-01 的 9:00-9:30 进行，标准学时为 30 分钟

    返回：包含 group_id 的 JSON，可用于后续添加小节
    """
    client = _get_client(session_id)

    # 处理默认值
    if category_ids is None:
        category_ids = []
    if tags is None:
        tags = []

    # 跟踪已上传的资源（用于失败时清理）
    uploaded_resources: list[tuple[str, str]] = []

    try:
        builder = CourseBuilder(client)

        # 1. 上传封面图（如有）
        cover_url = ""
        if cover_image_path:
            try:
                img_uploader = ImageUploader(client, client.base_url)
                cover_result = img_uploader.upload(cover_image_path, media_type="picweike")
                cover_url = cover_result.file_url
                uploaded_resources.append(("cover", cover_result.resource_id))
                logger.info("封面上传成功: %s", cover_url)
            except Exception as e:
                logger.warning("封面上传失败（非致命）: %s", e)

        # 2. 上传背景图（如有）
        bg_url = ""
        if bg_image_path:
            try:
                img_uploader = ImageUploader(client, client.base_url)
                bg_result = img_uploader.upload(bg_image_path, media_type="picweike")
                bg_url = bg_result.file_url
                uploaded_resources.append(("bg", bg_result.resource_id))
                logger.info("背景图上传成功: %s", bg_url)
            except Exception as e:
                logger.warning("背景图上传失败（非致命）: %s", e)

        # 3. 创建课程
        course = builder.create_course(
            title=title,
            desc_plain=desc_plain,
            desc_richtext=desc_richtext,
            cover_url=cover_url,
            bg_url=bg_url,
            category_ids=category_ids,
            category_names=category_names,
            tags=tags,
            start_date=start_date,
            start_time=start_time_str,
            end_time=end_time_str,
        )

        group_id = course["group_id"]

        # 保留旧字段兼容性，同时补充 course_url
        course["course_url"] = f"{client.base_url}/course/?groupId={group_id}"

        return _ok(
            data=course,
            next_action="proceed",
            suggested_action="课程创建成功。调用 tch_create_scorm_section 添加 SCORM 小节",
        )

    except Exception as e:
        logger.exception("创建课程失败")
        return _err(
            error_code="CREATE_COURSE_FAILED",
            error_message=str(e),
            suggested_action="检查参数后重试",
        )


@mcp.tool()
async def tch_create_scorm_section(
    group_id: Annotated[str, Field(description="课程 ID，来自 tch_create_course")],
    section_title: Annotated[str, Field(description="小节标题")],
    scorm_resource_id: Annotated[
        str | None,
        Field(
            default=None,
            description="已有 SCORM 资源 ID。如果提供，直接绑定已有资源；与 scorm_file_path 二选一",
        ),
    ] = None,
    scorm_file_path: Annotated[
        str | None,
        Field(
            default=None,
            description="本地 SCORM zip 文件路径。如果 scorm_resource_id 未提供，则必须提供此参数",
        ),
    ] = None,
    section_cover_path: Annotated[
        str | None,
        Field(default=None, description="小节封面图片路径（jpg/png），可选"),
    ] = None,
    duration_minutes: Annotated[
        int,
        Field(default=0, description="预计学习时长（分钟），可选"),
    ] = 0,
    is_required: Annotated[
        bool,
        Field(default=True, description="是否为必修小节"),
    ] = True,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """在课程中创建 SCORM 类型小节（sessionType=11，与视频微课共用码值）。

    触发条件：需要在课程中添加 SCORM 内容小节时调用。
    前置依赖：需先调用 tch_create_course 创建课程，或已有 group_id。

    SCORM 资源来源（二选一）：
    - 方式 A（推荐）：提供 scorm_resource_id，从已有的音视频资源库中选择
      → 先调用 tch_list_resources(ext_type="scorm") 查看已有资源
    - 方式 B：提供 scorm_file_path，上传新的 SCORM 包
      → 内部自动完成上传、注册、转码轮询全流程

    内部流程：
    1. 如无 scorm_resource_id，调用 ScormUploader 上传新 SCORM
    2. 如有小节封面，上传封面图到 COS
    3. 调用 savesession 创建小节并绑定资源（sectionArr 中包含 resource_video_id）
    4. 防御性调用 bind-upd 确保绑定关系

    返回：包含 session_id 的 JSON
    """
    client = _get_client(session_id)

    # 参数校验：必须提供 resource_id 或 file_path 之一
    if not scorm_resource_id and not scorm_file_path:
        return _err(
            error_code="MISSING_RESOURCE",
            error_message="必须提供 scorm_resource_id（已有 SCORM）或 scorm_file_path（上传新 SCORM）之一",
            suggested_action="调用 tch_list_resources(ext_type='scorm') 查看已有资源，或提供本地 SCORM zip 文件路径",
        )

    try:
        builder = CourseBuilder(client)

        # 1. 获取或上传 SCORM 资源
        actual_resource_id, err = await _upload_scorm_if_needed(
            client, scorm_file_path, scorm_resource_id, section_title
        )
        if err:
            return err
        actual_resource_id = actual_resource_id or ""

        # 2. 上传小节封面（如有）
        cover_resource_id, _ = _upload_image_if_needed(
            client, section_cover_path, media_type="picweike"
        )

        # 3. 创建小节
        session = builder.create_scorm_session(
            group_id=group_id,
            session_title=section_title,
            resource_id=actual_resource_id,
            cover_resource_id=cover_resource_id,
            duration_minutes=duration_minutes,
            is_required=is_required,
        )

        return _ok(
            data={
                "session_id": session["session_id"],
                "group_id": group_id,
                "title": section_title,
                "resource_id": actual_resource_id,
                "cover_resource_id": cover_resource_id or None,
                "is_required": is_required,
            },
            next_action="proceed",
            suggested_action="SCORM 小节创建成功。可继续添加更多小节，或在前端查看课程",
        )

    except RuntimeError as e:
        logger.error("创建 SCORM 小节失败: %s", e)
        return _err(
            error_code="CREATE_SCORM_SECTION_FAILED",
            error_message=str(e),
            suggested_action="检查参数和 SCORM 文件后重试",
        )
    except Exception as e:
        logger.exception("创建 SCORM 小节异常")
        return _err(
            error_code="CREATE_SCORM_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def tch_create_video_section(
    group_id: Annotated[str, Field(description="课程 ID，要添加小节的课程")],
    session_title: Annotated[str, Field(description="视频小节标题")],
    video_resource_id: Annotated[
        str,
        Field(description="视频资源 ID，从'我的音视频'中获取的 resource_id"),
    ],
    cover_image_path: Annotated[
        str | None,
        Field(
            default=None,
            description="封面图本地路径（jpg/png）。如果不提供，小节将使用视频默认缩略图作为封面。",
        ),
    ] = None,
    cover_resource_id: Annotated[
        str | None,
        Field(
            default=None,
            description="已上传的封面图资源 ID（与 cover_image_path 二选一）。如果提供了此值，将直接使用而不再上传新图片。",
        ),
    ] = None,
    desc_plain: Annotated[
        str | None,
        Field(
            default=None,
            description="纯文本视频说明。与 desc_richtext 二选一，不能同时提供。",
        ),
    ] = None,
    desc_richtext: Annotated[
        str | None,
        Field(
            default=None,
            description="富文本视频说明（HTML）。与 desc_plain 二选一，不能同时提供。",
        ),
    ] = None,
    is_required: Annotated[
        bool,
        Field(default=True, description="是否必修，默认 True"),
    ] = True,
    allow_drag_track: Annotated[
        bool,
        Field(default=True, description="是否允许学员拖动播放条，默认 True"),
    ] = True,
    allow_adjust_speed: Annotated[
        bool,
        Field(default=True, description="是否允许学员倍速播放，默认 True"),
    ] = True,
    min_duration_seconds: Annotated[
        int,
        Field(default=0, description="最小学习时长（秒），0 表示不设置"),
    ] = 0,
    max_duration_seconds: Annotated[
        int,
        Field(default=0, description="学习时长统计上限（秒），0 表示不设置"),
    ] = 0,
    desc_first_remind: Annotated[
        bool,
        Field(
            default=False,
            description="是否首次进入小节页弹出视频说明，默认 False",
        ),
    ] = False,
    tags: Annotated[
        list[str] | None,
        Field(default=None, description='视频标签列表，如 ["标签1", "标签2"]'),
    ] = None,
    sort_order: Annotated[
        int,
        Field(default=0, description="排序序号，0 表示自动追加到末尾"),
    ] = 0,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """在课程中创建视频类型小节（sessionType=11，与 SCORM 共用码值）并绑定视频资源.

    触发条件：需要向课程中添加一个视频小节时调用。
    前置依赖：需先调用 tch_login 完成登录，且视频资源已上传到"我的音视频"。

    视频小节特性：
    - 支持绑定已上传的音视频资源
    - 支持上传封面图或复用已上传的封面图资源
    - 支持纯文本或富文本视频说明
    - 支持设置学习规则：必修/选修、拖动播放条、倍速播放、学习时长限制
    - 支持首次进入弹出视频说明
    - 支持添加视频标签

    标准使用流程：
    1. 调用 tch_upload_audio_video 上传视频到"我的音视频"
    2. 调用 tch_create_video_section 创建视频小节并绑定资源
    3. 调用 tch_get_course_detail 验证小节创建结果

    返回：包含 session_id 和绑定资源信息的 JSON
    """
    client = _get_client(session_id)

    try:
        builder = CourseBuilder(client)
        result = builder.create_video_section(
            group_id=group_id,
            session_title=session_title,
            video_resource_id=video_resource_id,
            cover_image_path=cover_image_path or "",
            cover_resource_id=cover_resource_id or "",
            desc_plain=desc_plain or "",
            desc_richtext=desc_richtext or "",
            is_required=is_required,
            allow_drag_track=allow_drag_track,
            allow_adjust_speed=allow_adjust_speed,
            min_duration_seconds=min_duration_seconds,
            max_duration_seconds=max_duration_seconds,
            desc_first_remind=desc_first_remind,
            tags=tags,
            sort_order=sort_order,
        )

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="视频小节创建成功。如需继续添加更多小节，可再次调用；如需查看课程详情，调用 tch_get_course_detail；如需修改小节，调用 tch_update_scorm_section",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_PARAMS",
            error_message=str(e),
            suggested_action="请检查参数是否符合要求（如 desc_plain 和 desc_richtext 不能同时提供）",
        )
    except Exception as e:
        logger.exception("创建视频小节异常")
        return _err(
            error_code="CREATE_VIDEO_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def tch_update_video_section(
    group_id: Annotated[str, Field(description="课程 ID，包含要修改的小节的课程")],
    session_id: Annotated[str, Field(description="小节 ID，要修改的视频小节 ID")],
    session_title: Annotated[
        str | None,
        Field(default=None, description="新标题，None 表示不修改"),
    ] = None,
    video_resource_id: Annotated[
        str | None,
        Field(
            default=None,
            description="新视频资源 ID，None 表示不更换视频。需先从'我的音视频'获取。",
        ),
    ] = None,
    cover_image_path: Annotated[
        str | None,
        Field(
            default=None,
            description="新封面图本地路径（jpg/png）。None 表示不修改封面。",
        ),
    ] = None,
    cover_resource_id: Annotated[
        str | None,
        Field(
            default=None,
            description="已上传的封面图资源 ID。与 cover_image_path 二选一。",
        ),
    ] = None,
    remove_cover: Annotated[
        bool,
        Field(default=False, description="是否移除封面图（恢复为默认封面），默认 False"),
    ] = False,
    desc_plain: Annotated[
        str | None,
        Field(
            default=None,
            description="新纯文本视频说明。None 表示不修改。与 desc_richtext 二选一。",
        ),
    ] = None,
    desc_richtext: Annotated[
        str | None,
        Field(
            default=None,
            description="新富文本视频说明（HTML）。None 表示不修改。与 desc_plain 二选一。",
        ),
    ] = None,
    is_required: Annotated[
        bool | None,
        Field(
            default=None,
            description="是否必修。None 表示不修改。",
        ),
    ] = None,
    allow_drag_track: Annotated[
        bool | None,
        Field(
            default=None,
            description="是否允许学员拖动播放条。None 表示不修改。",
        ),
    ] = None,
    allow_adjust_speed: Annotated[
        bool | None,
        Field(
            default=None,
            description="是否允许学员倍速播放。None 表示不修改。",
        ),
    ] = None,
    min_duration_seconds: Annotated[
        int | None,
        Field(
            default=None,
            description="最小学习时长（秒）。None 表示不修改，0 表示取消限制。",
        ),
    ] = None,
    max_duration_seconds: Annotated[
        int | None,
        Field(
            default=None,
            description="学习时长统计上限（秒）。None 表示不修改，0 表示取消限制。",
        ),
    ] = None,
    desc_first_remind: Annotated[
        bool | None,
        Field(
            default=None,
            description="是否首次进入小节页弹出视频说明。None 表示不修改。",
        ),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Field(
            default=None,
            description='新标签列表。None 表示不修改，空列表 [] 表示清空标签。',
        ),
    ] = None,
    session_context_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """修改已有视频小节的属性.

    触发条件：需要修改课程中已有视频小节的任何属性时调用。
    前置依赖：需先调用 tch_login 完成登录。

    可修改的属性包括：
    - 标题、视频内容、封面图
    - 视频说明（纯文本或富文本）
    - 学习规则：必修/选修、拖动播放条、倍速播放、学习时长限制
    - 首次进入弹出视频说明
    - 标签

    所有参数均为可选（None 表示不修改），可单独修改任意一个或多个属性。

    标准使用流程：
    1. 调用 tch_get_course_detail 获取课程详情和小节列表
    2. 调用 tch_update_video_section 修改目标小节
    3. 调用 tch_get_course_detail 验证修改结果

    返回：包含修改字段列表和更新后信息的 JSON
    """
    client = _get_client(session_context_id)

    try:
        builder = CourseBuilder(client)
        result = builder.update_video_section(
            group_id=group_id,
            session_id=session_id,
            session_title=session_title,
            video_resource_id=video_resource_id,
            cover_image_path=cover_image_path,
            cover_resource_id=cover_resource_id,
            remove_cover=remove_cover,
            desc_plain=desc_plain,
            desc_richtext=desc_richtext,
            is_required=is_required,
            allow_drag_track=allow_drag_track,
            allow_adjust_speed=allow_adjust_speed,
            min_duration_seconds=min_duration_seconds,
            max_duration_seconds=max_duration_seconds,
            desc_first_remind=desc_first_remind,
            tags=tags,
        )

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="视频小节修改成功。如需查看修改结果，调用 tch_get_course_detail；如需继续修改其他小节，可再次调用。",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_PARAMS",
            error_message=str(e),
            suggested_action="请检查参数是否有冲突（如 desc_plain 和 desc_richtext 不能同时提供）",
        )
    except RuntimeError as e:
        return _err(
            error_code="UPDATE_VIDEO_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查小节 ID 是否正确，以及目标小节是否为视频类型",
        )
    except Exception as e:
        logger.exception("修改视频小节异常")
        return _err(
            error_code="UPDATE_VIDEO_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def tch_create_article_section(
    group_id: Annotated[str, Field(description="课程 ID，要添加文章小节的课程")],
    session_title: Annotated[str, Field(description="文章小节标题")],
    article_content: Annotated[
        str,
        Field(description="文章 HTML 内容。支持完整的 HTML 格式，包括标题、段落、表格、图片等。"),
    ],
    cover_image_path: Annotated[
        str | None,
        Field(
            default=None,
            description="封面图本地路径（jpg/png）。如果不提供，小节将使用默认封面。",
        ),
    ] = None,
    cover_resource_id: Annotated[
        str | None,
        Field(
            default=None,
            description="已上传的封面图资源 ID（与 cover_image_path 二选一）。",
        ),
    ] = None,
    is_required: Annotated[
        bool,
        Field(default=True, description="是否必修，默认 True"),
    ] = True,
    type_name: Annotated[
        str,
        Field(default="", description='小节类型标签，如 "导学"、"案例分析"、"总结"'),
    ] = "",
    min_duration_seconds: Annotated[
        int,
        Field(default=0, description="最小学习时长（秒），0 表示不设置"),
    ] = 0,
    max_duration_seconds: Annotated[
        int,
        Field(default=0, description="学习时长统计上限（秒），0 表示不设置"),
    ] = 0,
    show_course_creator_info: Annotated[
        bool,
        Field(default=True, description="是否展示课程创建者信息，默认 True"),
    ] = True,
    show_article_reading_speed: Annotated[
        bool,
        Field(default=True, description="是否展示文章字数和阅读速度，默认 True"),
    ] = True,
    is_comment_time_visible: Annotated[
        bool,
        Field(default=True, description="是否允许学员查看发言的提交时间，默认 True"),
    ] = True,
    enable_comment: Annotated[
        bool,
        Field(default=True, description="是否开启发言区，默认 True"),
    ] = True,
    tags: Annotated[
        list[str] | None,
        Field(default=None, description='文章标签列表，如 ["标签1", "标签2"]'),
    ] = None,
    sort_order: Annotated[
        int,
        Field(default=0, description="排序序号，0 表示自动追加到末尾"),
    ] = 0,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """在课程中创建文章类型小节（sessionType=13）.

    触发条件：需要向课程中添加一个文章小节时调用。
    前置依赖：需先调用 tch_login 完成登录。

    文章小节特性：
    - 支持富文本 HTML 内容（标题、段落、表格、图片等）
    - 支持上传封面图或复用已上传的封面图资源
    - 支持设置学习规则：必修/选修、学习时长限制
    - 支持展示课程创建者信息、文章字数和阅读速度
    - 支持发言区设置
    - 支持小节类型标签和文章标签

    返回：包含 session_id 和绑定资源信息的 JSON
    """
    client = _get_client(session_id)

    try:
        builder = CourseBuilder(client)
        result = builder.create_article_section(
            group_id=group_id,
            session_title=session_title,
            article_content=article_content,
            cover_image_path=cover_image_path or "",
            cover_resource_id=cover_resource_id or "",
            is_required=is_required,
            type_name=type_name,
            min_duration_seconds=min_duration_seconds,
            max_duration_seconds=max_duration_seconds,
            show_course_creator_info=show_course_creator_info,
            show_article_reading_speed=show_article_reading_speed,
            is_comment_time_visible=is_comment_time_visible,
            enable_comment=enable_comment,
            tags=tags,
            sort_order=sort_order,
        )

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="文章小节创建成功。如需继续添加更多小节，可再次调用；如需查看课程详情，调用 tch_get_course_detail；如需修改小节，调用 tch_update_article_section",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_PARAMS",
            error_message=str(e),
            suggested_action="请检查参数是否符合要求（如 cover_image_path 和 cover_resource_id 不能同时提供）",
        )
    except Exception as e:
        logger.exception("创建文章小节异常")
        return _err(
            error_code="CREATE_ARTICLE_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def tch_update_article_section(
    group_id: Annotated[str, Field(description="课程 ID，包含要修改的小节的课程")],
    session_id: Annotated[str, Field(description="小节 ID，要修改的文章小节 ID")],
    session_title: Annotated[
        str | None,
        Field(default=None, description="新标题，None 表示不修改"),
    ] = None,
    article_content: Annotated[
        str | None,
        Field(
            default=None,
            description="新文章 HTML 内容。None 表示不修改。",
        ),
    ] = None,
    cover_image_path: Annotated[
        str | None,
        Field(
            default=None,
            description="新封面图本地路径（jpg/png）。None 表示不修改封面。",
        ),
    ] = None,
    cover_resource_id: Annotated[
        str | None,
        Field(
            default=None,
            description="已上传的封面图资源 ID。与 cover_image_path 二选一。",
        ),
    ] = None,
    remove_cover: Annotated[
        bool,
        Field(default=False, description="是否移除封面图（恢复为默认封面），默认 False"),
    ] = False,
    is_required: Annotated[
        bool | None,
        Field(default=None, description="是否必修。None 表示不修改。"),
    ] = None,
    type_name: Annotated[
        str | None,
        Field(default=None, description="小节类型标签。None 表示不修改。"),
    ] = None,
    min_duration_seconds: Annotated[
        int | None,
        Field(
            default=None,
            description="最小学习时长（秒）。None 表示不修改，0 表示取消限制。",
        ),
    ] = None,
    max_duration_seconds: Annotated[
        int | None,
        Field(
            default=None,
            description="学习时长统计上限（秒）。None 表示不修改，0 表示取消限制。",
        ),
    ] = None,
    show_course_creator_info: Annotated[
        bool | None,
        Field(default=None, description="是否展示课程创建者信息。None 表示不修改。"),
    ] = None,
    show_article_reading_speed: Annotated[
        bool | None,
        Field(default=None, description="是否展示文章字数和阅读速度。None 表示不修改。"),
    ] = None,
    is_comment_time_visible: Annotated[
        bool | None,
        Field(default=None, description="是否允许学员查看发言提交时间。None 表示不修改。"),
    ] = None,
    enable_comment: Annotated[
        bool | None,
        Field(default=None, description="是否开启发言区。None 表示不修改。"),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Field(
            default=None,
            description='新标签列表。None 表示不修改，空列表 [] 表示清空标签。',
        ),
    ] = None,
    session_context_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """修改已有文章小节的属性.

    触发条件：需要修改课程中已有文章小节的任何属性时调用。
    前置依赖：需先调用 tch_login 完成登录。

    可修改的属性包括：
    - 标题、文章内容、封面图
    - 学习规则：必修/选修、学习时长限制
    - 展示课程创建者信息、文章字数和阅读速度
    - 发言区设置、发言提交时间可见性
    - 小节类型标签、文章标签

    所有参数均为可选（None 表示不修改），可单独修改任意一个或多个属性。

    返回：包含修改字段列表和更新后信息的 JSON
    """
    client = _get_client(session_context_id)

    try:
        builder = CourseBuilder(client)
        result = builder.update_article_section(
            group_id=group_id,
            session_id=session_id,
            session_title=session_title,
            article_content=article_content,
            cover_image_path=cover_image_path,
            cover_resource_id=cover_resource_id,
            remove_cover=remove_cover,
            is_required=is_required,
            type_name=type_name,
            min_duration_seconds=min_duration_seconds,
            max_duration_seconds=max_duration_seconds,
            show_course_creator_info=show_course_creator_info,
            show_article_reading_speed=show_article_reading_speed,
            is_comment_time_visible=is_comment_time_visible,
            enable_comment=enable_comment,
            tags=tags,
        )

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="文章小节修改成功。如需查看修改结果，调用 tch_get_course_detail；如需继续修改其他小节，可再次调用。",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_PARAMS",
            error_message=str(e),
            suggested_action="请检查参数是否有冲突（如 cover_image_path 和 cover_resource_id 不能同时提供）",
        )
    except RuntimeError as e:
        return _err(
            error_code="UPDATE_ARTICLE_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查小节 ID 是否正确，以及目标小节是否为文章类型",
        )
    except Exception as e:
        logger.exception("修改文章小节异常")
        return _err(
            error_code="UPDATE_ARTICLE_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def tch_create_infographic_section(
    group_id: Annotated[str, Field(description="课程 ID，来自 tch_create_course")],
    session_title: Annotated[str, Field(description="图文小节标题")],
    content_blocks: Annotated[
        list[dict],
        Field(
            description='图文内容块列表，每项为 {"type": "image"|"text", "content": "..."}。'
                        "图片可以是本地路径（自动上传）或已上传的 URL。"
                        '例如：[{"type": "image", "content": "/path/to/img.jpg"}, {"type": "text", "content": "文字说明"}]'
        ),
    ],
    cover_image_path: Annotated[
        str | None,
        Field(
            default=None,
            description="封面图本地路径（jpg/png）。如果不提供，小节将使用默认封面。",
        ),
    ] = None,
    cover_resource_id: Annotated[
        str | None,
        Field(
            default=None,
            description="已上传的封面图资源 ID（与 cover_image_path 二选一）。",
        ),
    ] = None,
    is_required: Annotated[
        bool,
        Field(default=True, description="是否必修，默认 True"),
    ] = True,
    type_name: Annotated[
        str,
        Field(default="", description='小节类型标签，如 "导学"、"案例分析"'),
    ] = "",
    min_duration_seconds: Annotated[
        int,
        Field(default=0, description="最小学习时长（秒），0 表示不设置"),
    ] = 0,
    max_duration_seconds: Annotated[
        int,
        Field(default=0, description="学习时长统计上限（秒），0 表示不设置"),
    ] = 0,
    show_course_creator_info: Annotated[
        bool,
        Field(default=True, description="是否展示课程创建者信息，默认 True"),
    ] = True,
    show_article_reading_speed: Annotated[
        bool,
        Field(default=True, description="是否展示阅读速度，默认 True"),
    ] = True,
    is_comment_time_visible: Annotated[
        bool,
        Field(default=True, description="是否允许学员查看发言提交时间，默认 True"),
    ] = True,
    enable_comment: Annotated[
        bool,
        Field(default=True, description="是否开启发言区，默认 True"),
    ] = True,
    tags: Annotated[
        list[str] | None,
        Field(default=None, description='标签列表，如 ["标签1", "标签2"]'),
    ] = None,
    sort_order: Annotated[
        int,
        Field(default=0, description="排序序号，0 表示自动追加到末尾"),
    ] = 0,
    session_context_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """在课程中创建图文类型小节（sessionType=15）.

    触发条件：需要向课程中添加一个图文小节时调用。
    前置依赖：需先调用 tch_login 完成登录。

    图文小节特性：
    - 支持图片和文字混排，可自由调整顺序
    - 图片支持本地路径上传或复用已上传的 URL
    - 支持设置学习规则：必修/选修、学习时长限制
    - 支持展示课程创建者信息、阅读速度
    - 支持发言区设置
    - 支持小节类型标签和标签

    内容块格式：
    - {"type": "image", "content": "图片路径或URL"} — 图片块
    - {"type": "text", "content": "文字内容"} — 文字块

    返回：包含 session_id 和绑定资源信息的 JSON
    """
    client = _get_client(session_context_id)

    try:
        builder = CourseBuilder(client)
        result = builder.create_infographic_section(
            group_id=group_id,
            session_title=session_title,
            content_blocks=content_blocks,
            cover_image_path=cover_image_path or "",
            cover_resource_id=cover_resource_id or "",
            is_required=is_required,
            type_name=type_name,
            min_duration_seconds=min_duration_seconds,
            max_duration_seconds=max_duration_seconds,
            show_course_creator_info=show_course_creator_info,
            show_article_reading_speed=show_article_reading_speed,
            is_comment_time_visible=is_comment_time_visible,
            enable_comment=enable_comment,
            tags=tags,
            sort_order=sort_order,
        )

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action=f"图文小节创建成功。session_id={result.get('session_id')}, resource_imgText_id={result.get('resource_imgText_id')}。"
            "【重要】请保存 resource_imgText_id，修改图文内容时必需提供。"
            "如需继续添加更多小节，可再次调用；如需查看课程详情，调用 tch_get_course_detail；"
            "如需修改小节，调用 tch_update_infographic_section（需传入 resource_imgText_id）。"
            "如已丢失 resource_imgText_id，可先调用 tch_get_infographic_content 获取。",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_PARAMS",
            error_message=str(e),
            suggested_action="请检查参数是否符合要求（如 cover_image_path 和 cover_resource_id 不能同时提供）",
        )
    except Exception as e:
        logger.exception("创建图文小节异常")
        return _err(
            error_code="CREATE_INFOGRAPHIC_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def tch_get_infographic_content(
    resource_imgText_id: Annotated[
        str,
        Field(description="图文内容资源 ID。创建图文小节时返回，修改内容时必需。"),
    ],
    session_context_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取图文小节的内容列表.

    触发条件：需要查看图文小节现有内容、或获取 resource_imgText_id 时调用。
    前置依赖：需先调用 tch_login 完成登录。

    注意：getsessionbaseinfo 不返回 resource_imgText_id，必须从创建结果中保存。
    如已丢失，可通过前端界面查看或重新创建图文小节。

    返回：JSON 格式的内容列表，含 content_blocks、统计信息。
    """
    client = _get_client(session_context_id)

    try:
        builder = CourseBuilder(client)
        items = builder._get_imgtextlist(resource_imgText_id)

        # 转换为 content_blocks 格式
        content_blocks = []
        for item in items:
            item_type = item.get("type", "")
            content = item.get("content", "")
            if item_type == "img":
                content_blocks.append({"type": "image", "content": content})
            else:
                content_blocks.append({"type": "text", "content": content})

        image_count = sum(1 for b in content_blocks if b["type"] == "image")
        text_count = sum(1 for b in content_blocks if b["type"] == "text")

        return _ok(
            data={
                "resource_imgText_id": resource_imgText_id,
                "content_blocks": content_blocks,
                "total_count": len(content_blocks),
                "image_count": image_count,
                "text_count": text_count,
            },
            next_action="proceed",
            suggested_action=f"获取成功：共 {len(content_blocks)} 项（{image_count} 张图片 + {text_count} 段文字）。"
            "如需修改内容，调用 tch_update_infographic_section（需传入 resource_imgText_id）；"
            "如需替换图片，在 content_blocks 中修改 image 项的 content 为新的本地路径或 URL。",
        )
    except Exception as e:
        logger.exception("获取图文内容异常")
        return _err(
            error_code="GET_INFOGRAPHIC_CONTENT_ERROR",
            error_message=str(e),
            suggested_action="请检查 resource_imgText_id 是否正确。如已丢失，无法通过 session_id 获取，需从创建时的结果中找回。",
        )


@mcp.tool()
async def tch_update_infographic_section(
    group_id: Annotated[str, Field(description="课程 ID，包含要修改的小节的课程")],
    session_id: Annotated[str, Field(description="小节 ID，要修改的图文小节 ID")],
    session_title: Annotated[
        str | None,
        Field(default=None, description="新标题，None 表示不修改"),
    ] = None,
    content_blocks: Annotated[
        list[dict] | None,
        Field(
            default=None,
            description='新图文内容列表，None 表示不修改。每项为 {"type": "image"|"text", "content": "..."}'
                        "图片可以是本地路径（自动上传）或已上传的 URL。",
        ),
    ] = None,
    cover_image_path: Annotated[
        str | None,
        Field(
            default=None,
            description="新封面图本地路径（jpg/png）。None 表示不修改封面。",
        ),
    ] = None,
    cover_resource_id: Annotated[
        str | None,
        Field(
            default=None,
            description="已上传的封面图资源 ID。与 cover_image_path 二选一。",
        ),
    ] = None,
    remove_cover: Annotated[
        bool,
        Field(default=False, description="是否移除封面图（恢复为默认封面），默认 False"),
    ] = False,
    is_required: Annotated[
        bool | None,
        Field(default=None, description="是否必修。None 表示不修改。"),
    ] = None,
    type_name: Annotated[
        str | None,
        Field(default=None, description="小节类型标签。None 表示不修改。"),
    ] = None,
    min_duration_seconds: Annotated[
        int | None,
        Field(
            default=None,
            description="最小学习时长（秒）。None 表示不修改，0 表示取消限制。",
        ),
    ] = None,
    max_duration_seconds: Annotated[
        int | None,
        Field(
            default=None,
            description="学习时长统计上限（秒）。None 表示不修改，0 表示取消限制。",
        ),
    ] = None,
    show_course_creator_info: Annotated[
        bool | None,
        Field(default=None, description="是否展示课程创建者信息。None 表示不修改。"),
    ] = None,
    show_article_reading_speed: Annotated[
        bool | None,
        Field(default=None, description="是否展示阅读速度。None 表示不修改。"),
    ] = None,
    is_comment_time_visible: Annotated[
        bool | None,
        Field(default=None, description="是否允许学员查看发言提交时间。None 表示不修改。"),
    ] = None,
    enable_comment: Annotated[
        bool | None,
        Field(default=None, description="是否开启发言区。None 表示不修改。"),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Field(
            default=None,
            description='新标签列表。None 表示不修改，空列表 [] 表示清空标签。',
        ),
    ] = None,
    resource_imgText_id: Annotated[
        str | None,
        Field(
            default=None,
            description="图文内容资源 ID。修改图文内容时必需。如未知，可先调用 tch_get_infographic_content 获取。",
        ),
    ] = None,
    session_context_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """修改已有图文小节的属性.

    触发条件：需要修改图文小节的标题、内容、封面、学习规则等时调用。
    前置依赖：需先调用 tch_login 完成登录，并确认小节为图文类型。

    【重要】修改图文内容（content_blocks）时必须提供 resource_imgText_id。
    该字段在创建时由 tch_create_infographic_section 返回，getsessionbaseinfo 不返回此字段。
    如已丢失，无法通过 session_id 获取，需从创建结果中找回。

    修改策略：只修改提供的参数（非 None），未提供的参数保持不变。
    内容块格式与创建时相同：{"type": "image"|"text", "content": "..."}。
    图片 content 可以是本地路径（自动上传）或已上传的 URL。

    返回：包含修改字段列表的 JSON
    """
    client = _get_client(session_context_id)

    try:
        builder = CourseBuilder(client)
        result = builder.update_infographic_section(
            group_id=group_id,
            session_id=session_id,
            session_title=session_title,
            content_blocks=content_blocks,
            cover_image_path=cover_image_path,
            cover_resource_id=cover_resource_id,
            remove_cover=remove_cover,
            is_required=is_required,
            type_name=type_name,
            min_duration_seconds=min_duration_seconds,
            max_duration_seconds=max_duration_seconds,
            show_course_creator_info=show_course_creator_info,
            show_article_reading_speed=show_article_reading_speed,
            is_comment_time_visible=is_comment_time_visible,
            enable_comment=enable_comment,
            tags=tags,
            resource_imgText_id=resource_imgText_id,
        )

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="图文小节修改成功。如需查看修改结果，调用 tch_get_course_detail；如需继续修改其他小节，可再次调用。",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_PARAMS",
            error_message=str(e),
            suggested_action="请检查参数是否有冲突（如 cover_image_path 和 cover_resource_id 不能同时提供）",
        )
    except RuntimeError as e:
        err_msg = str(e)
        if "resource_imgText_id" in err_msg:
            suggested = "修改图文内容必须提供 resource_imgText_id。该字段在创建时返回，getsessionbaseinfo 不返回此字段。请从创建结果中找回。"
        else:
            suggested = "请检查小节 ID 是否正确，以及目标小节是否为图文类型。"
        return _err(
            error_code="UPDATE_INFOGRAPHIC_SECTION_ERROR",
            error_message=err_msg,
            suggested_action=suggested,
        )
    except Exception as e:
        logger.exception("修改图文小节异常")
        return _err(
            error_code="UPDATE_INFOGRAPHIC_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def tch_create_document_section(
    group_id: Annotated[str, Field(description="课程 ID，来自 tch_create_course")],
    section_title: Annotated[str, Field(description="小节标题")],
    document_resource_id: Annotated[
        str | None,
        Field(
            default=None,
            description="已有文档资源 ID。如果提供，直接绑定已有文档；与 document_file_path 二选一",
        ),
    ] = None,
    document_file_path: Annotated[
        str | None,
        Field(
            default=None,
            description="本地文档文件路径。支持 .ppt/.pptx/.xls/.xlsx/.doc/.docx/.pdf/.txt/.xlsm。"
                        "如果 document_resource_id 未提供，则必须提供此参数。文件大小限制 100MB。",
        ),
    ] = None,
    desc_plain: Annotated[
        str,
        Field(
            default="",
            description="纯文本文档说明。与 desc_richtext 二选一，不建议同时提供。",
        ),
    ] = "",
    desc_richtext: Annotated[
        str,
        Field(
            default="",
            description="富文本文档说明（HTML 格式）。与 desc_plain 二选一。"
                        "提供时会创建富文本内容并绑定到小节。",
        ),
    ] = "",
    is_required: Annotated[
        bool,
        Field(default=True, description="是否为必修小节（True=必修, False=选修）"),
    ] = True,
    allow_download: Annotated[
        bool,
        Field(default=True, description="是否允许学员下载文档"),
    ] = True,
    min_duration_minutes: Annotated[
        int,
        Field(
            default=0,
            description="最小学习时长（分钟），0=不限制。学员需学习达到此时长才算完成。",
        ),
    ] = 0,
    finish_condition: Annotated[
        str,
        Field(
            default="open",
            description='完成条件: "open"=打开文档即完成（默认）, "last_page"=学完文档最后一页才算完成',
        ),
    ] = "open",
    show_creator_info: Annotated[
        bool,
        Field(default=True, description="是否展示课程创建者信息"),
    ] = True,
    enable_comment: Annotated[
        bool,
        Field(default=True, description="是否开启发言区"),
    ] = True,
    show_comment_time: Annotated[
        bool,
        Field(default=True, description="是否允许查看发言提交时间"),
    ] = True,
    tags: Annotated[
        list[str] | None,
        Field(default=None, description="标签文本列表，如 ['文档标签', '培训']"),
    ] = None,
    section_cover_path: Annotated[
        str | None,
        Field(default=None, description="小节封面图片路径（jpg/png），可选"),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """在课程中创建文档类型的小节.

    触发条件：需要在课程中添加 PPT/Excel/Word/PDF/TXT 文档小节时调用。
    前置依赖：需先调用 tch_create_course 创建课程，或已有 group_id。

    文档资源来源（二选一）：
    - 方式 A（推荐）：提供 document_resource_id，从"我的文档"资源库中选择
      → 先调用 tch_list_documents 查看已有文档
    - 方式 B：提供 document_file_path，上传新文档
      → 内部自动完成上传、注册全流程（文件大小限制 100MB）

    文档说明支持两种方式（二选一）：
    - 纯文本：提供 desc_plain → 直接写入 desc 字段
    - 富文本：提供 desc_richtext（HTML）→ 创建富文本内容并绑定

    内部流程：
    1. 如无 document_resource_id，调用 DocumentUploader 上传新文档
    2. 如有小节封面，上传封面图到 COS
    3. 如提供富文本说明，创建 multimedia 富文本内容
    4. 调用 savesession 创建文档小节（sessionType=14）
    5. 文档小节不需要 bind-upd，资源在 savesession 中直接绑定

    返回：包含 session_id 的 JSON
    """
    client = _get_client(session_id)

    # 参数校验：必须提供 resource_id 或 file_path 之一
    if not document_resource_id and not document_file_path:
        return _err(
            error_code="MISSING_RESOURCE",
            error_message="必须提供 document_resource_id（已有文档）或 document_file_path（上传新文档）之一",
            suggested_action="调用 tch_list_documents 查看已有文档，或提供本地文档文件路径",
        )

    # 文档说明方式校验
    if desc_plain and desc_richtext:
        return _err(
            error_code="MUTUALLY_EXCLUSIVE",
            error_message="desc_plain 和 desc_richtext 不能同时提供",
            suggested_action="二选一：使用纯文本说明或富文本说明",
        )

    # 完成条件校验
    if finish_condition not in ("open", "last_page"):
        return _err(
            error_code="INVALID_FINISH_CONDITION",
            error_message=f"finish_condition 必须是 'open' 或 'last_page'，收到: {finish_condition}",
            suggested_action="使用 'open'（打开即完成）或 'last_page'（学完最后一页）",
        )

    try:
        builder = CourseBuilder(client)

        # 1. 获取或上传文档资源
        actual_resource_id, err = await _upload_document_if_needed(
            client, document_file_path, document_resource_id, section_title
        )
        if err:
            return err
        actual_resource_id = actual_resource_id or ""

        # 2. 上传小节封面（如有）
        cover_resource_id, _ = _upload_image_if_needed(
            client, section_cover_path, media_type="picweike"
        )

        # 3. 创建文档小节
        session = builder.create_document_session(
            group_id=group_id,
            session_title=section_title,
            resource_id=actual_resource_id,
            desc_plain=desc_plain,
            desc_richtext=desc_richtext,
            is_required=is_required,
            allow_download=allow_download,
            min_duration_seconds=min_duration_minutes * 60,
            finish_condition=finish_condition,
            show_creator_info=show_creator_info,
            enable_comment=enable_comment,
            show_comment_time=show_comment_time,
            tags=tags,
            cover_resource_id=cover_resource_id,
        )

        return _ok(
            data={
                "session_id": session["session_id"],
                "group_id": group_id,
                "title": section_title,
                "resource_id": actual_resource_id,
                "cover_resource_id": cover_resource_id or None,
                "is_required": is_required,
                "allow_download": allow_download,
                "min_duration_minutes": min_duration_minutes,
                "finish_condition": finish_condition,
                "multimedia_type": session.get("multimedia_type", 0),
                "multimedia_id": session.get("multimedia_id", 0),
            },
            next_action="proceed",
            suggested_action=f"文档小节 '{section_title}' 创建成功 (session_id={session.get('session_id', '')})。"
                        f"接下来可调用 tch_list_sections(group_id='{group_id}') 验证小节列表，"
                        f"或 tch_get_section(section_id='{session.get('session_id', '')}') 查看完整详情。"
                        f"如需添加更多小节，可继续调用 tch_create_document_section。",
        )

    except ValueError as e:
        logger.error("创建文档小节参数错误: %s", e)
        return _err(
            error_code="INVALID_PARAMS",
            error_message=str(e),
            suggested_action="检查参数格式是否符合要求",
        )
    except RuntimeError as e:
        logger.error("创建文档小节失败: %s", e)
        return _err(
            error_code="CREATE_DOCUMENT_SECTION_FAILED",
            error_message=str(e),
            suggested_action="检查参数和文档文件后重试",
        )
    except Exception as e:
        logger.exception("创建文档小节异常")
        return _err(
            error_code="CREATE_DOCUMENT_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def tch_create_survey_section(
    group_id: Annotated[str, Field(description="课程 ID，来自 tch_create_course")],
    session_title: Annotated[str, Field(description="问卷小节标题")],
    questions_json: Annotated[
        str,
        Field(
            description='题目列表的 JSON 字符串。每项为一个题目对象，支持 5 种类型。'
                        '题目和段落说明可以按任意顺序混合排列（段落说明可放在题目前、后或之间）。\n'
                        '**单选题**：{"type":"radio","title":"题目","required":true,"options":["A","B"]}\n'
                        '**多选题**：{"type":"checkbox","title":"题目","required":true,"options":["A","B"],"min_options":1,"max_options":2}\n'
                        '**填空题**：{"type":"textarea","title":"题目","required":true}\n'
                        '**量值题**：{"type":"number","title":"评分","required":true,"min_value":1,"max_value":5,"min_label":"差","max_label":"好"}\n'
                        '**段落说明**：{"type":"paragraph","content":"<p>说明文字</p>"} — 可放在任意位置，用于分隔题目或添加辅助说明\n'
                        '单选/多选支持 extra_answer: {"label":"其他","required":false}'
        ),
    ],
    is_required: Annotated[
        bool,
        Field(default=True, description="是否必修（True=必修, False=选修）"),
    ] = True,
    jump_button: Annotated[
        bool,
        Field(default=False, description="提交成功后是否显示跳转按钮"),
    ] = False,
    jump_url: Annotated[
        str,
        Field(default="", description="跳转按钮的目标 URL（jump_button=True 时有效）"),
    ] = "",
    jump_button_title: Annotated[
        str,
        Field(default="", description="跳转按钮的文本"),
    ] = "",
    show_user_result: Annotated[
        bool,
        Field(default=False, description="提交后是否展示问卷结果"),
    ] = False,
    is_show_participate_on_screen: Annotated[
        bool,
        Field(default=True, description="大屏幕是否展示参与人数"),
    ] = True,
    share_status: Annotated[
        int,
        Field(
            default=1,
            description="问卷访问权限: 1=课程内公开(默认), 2=企业内公开, 3=仅自己",
        ),
    ] = 1,
    submit_permission: Annotated[
        int,
        Field(
            default=4,
            description="提交权限: 3=不允许匿名/必须登录, 4=允许匿名提交(默认)",
        ),
    ] = 1,
    allow_modify: Annotated[
        bool,
        Field(default=False, description="是否允许提交后修改问卷"),
    ] = False,
    submit_limit: Annotated[
        str,
        Field(default="1", description='提交次数限制: "1"=最多1次(默认), "n"=允许多次'),
    ] = "1",
    result_prompt: Annotated[
        str,
        Field(default="感谢您的参与！", description="提交成功提示语"),
    ] = "感谢您的参与！",
    accept_submission_time: Annotated[
        int,
        Field(default=0, description="开始提交时间（Unix时间戳，0=不限制）"),
    ] = 0,
    refuse_submission_time: Annotated[
        int,
        Field(default=0, description="结束提交时间（Unix时间戳，0=不限制）"),
    ] = 0,
    random_option: Annotated[
        bool,
        Field(default=False, description="选项是否随机展示"),
    ] = False,
    type_name: Annotated[
        str,
        Field(default="", description='小节类型标签，如"问卷"、"调研"'),
    ] = "",
    tags: Annotated[
        list[str] | None,
        Field(default=None, description="标签文本列表"),
    ] = None,
    sort_order: Annotated[
        int,
        Field(default=0, description="排序序号，0 表示自动追加到末尾"),
    ] = 0,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """在课程中创建问卷类型小节（sessionType=1）.

    触发条件：需要向课程中添加问卷/调研/投票小节时调用。
    前置依赖：需先调用 tch_login 完成登录，已有 group_id。

    问卷支持 5 种题目类型：单选题、多选题、简答式填空、量值题（评分）、段落说明。
    题目通过 questions_json 参数传入，为 JSON 字符串格式。

    返回：包含 session_id 和题目数量的 JSON
    """
    client = _get_client(session_id)

    try:
        # 解析题目 JSON
        questions = json.loads(questions_json)
        if not isinstance(questions, list):
            raise ValueError("questions_json 必须解析为列表")

        builder = CourseBuilder(client)
        result = builder.create_survey_section(
            group_id=group_id,
            session_title=session_title,
            questions=questions,
            is_required=is_required,
            jump_button=jump_button,
            jump_url=jump_url,
            jump_button_title=jump_button_title,
            show_user_result=show_user_result,
            is_show_participate_on_screen=is_show_participate_on_screen,
            share_status=share_status,
            submit_permission=submit_permission,
            allow_modify=allow_modify,
            submit_limit=submit_limit,
            result_prompt=result_prompt,
            accept_submission_time=accept_submission_time,
            refuse_submission_time=refuse_submission_time,
            random_option=random_option,
            type_name=type_name,
            tags=tags,
            sort_order=sort_order,
        )

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="问卷小节创建成功。如需继续添加更多小节，可再次调用；"
                            "如需查看课程详情，调用 tch_get_course_detail。",
        )
    except json.JSONDecodeError as e:
        return _err(
            error_code="INVALID_QUESTIONS_JSON",
            error_message=f"questions_json 解析失败: {e}",
            suggested_action="请检查 questions_json 是否为有效的 JSON 格式。",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_PARAMS",
            error_message=str(e),
            suggested_action="请检查题目格式是否符合要求（如单选题必须提供 options）",
        )
    except Exception as e:
        logger.exception("创建问卷小节异常")
        return _err(
            error_code="CREATE_SURVEY_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def tch_create_exam_section(
    group_id: Annotated[str, Field(description="课程 ID，来自 tch_create_course")],
    session_title: Annotated[str, Field(description="考试小节标题")],
    questions_json: Annotated[
        str,
        Field(
            description='题目列表的 JSON 字符串。每项为一个题目对象，支持 3 种类型。\n'
                        '**单选题**：{"type":"radio","title":"题目","score":5,"options":["A","B","C"],"correct_indices":[2]}\n'
                        '  - correct_indices: 正确选项的索引数组（0-based），如 [2] 表示第3个选项正确\n'
                        '  - explanation: 答案说明（可选）\n'
                        '  - difficulty: 难度 1=低(默认), 2=中, 3=高\n'
                        '**多选题 - 全部正确才得分**：{"type":"checkbox","title":"题目","score":7,"options":["A","B","C","D"],"correct_indices":[0,1,2,3],"scoring_rule":"all_correct"}\n'
                        '  - scoring_rule: "all_correct"=全部正确才得分（默认）\n'
                        '**多选题 - 部分正确得分**：{"type":"checkbox","title":"题目","score":10,"options":["A","B","C","D","E"],"correct_indices":[0,1,2,3],"scoring_rule":"partial","partial_score":6}\n'
                        '  - scoring_rule: "partial"=部分正确得分\n'
                        '  - partial_score: 少选得分（必须 > 0）\n'
                        '**开放题**：{"type":"input","title":"题目","score":10,"standard_answers":["答案1","答案2"]}\n'
                        '  - standard_answers: 标准答案列表（可选）。设置后学员提交与任一标准答案一致则自动得分\n'
                        '  - 不设置 standard_answers（或传空数组）时，学员提交后需 teacher 手动评分'
        ),
    ],
    description: Annotated[
        str,
        Field(default="", description="考试说明/描述，学员进入考试前展示的内容"),
    ] = "",
    exam_duration_minutes: Annotated[
        int,
        Field(default=0, ge=0, description="考试时长（分钟），0=不限时"),
    ] = 0,
    quiz_count_limit: Annotated[
        int,
        Field(default=0, ge=0, description="考试次数限制，0=不限次数"),
    ] = 0,
    quiz_pass_mark: Annotated[
        int,
        Field(default=0, ge=0, le=100, description="及格线（百分比 0-100），0=不设及格线"),
    ] = 0,
    random_option: Annotated[
        bool,
        Field(default=False, description="是否随机展示选项顺序"),
    ] = False,
    show_user_result: Annotated[
        bool,
        Field(default=True, description="是否向学员展示成绩"),
    ] = True,
    submit_one_by_one: Annotated[
        bool,
        Field(default=False, description="是否逐题提交。True=逐题提交，False=整卷提交"),
    ] = False,
    is_required: Annotated[
        bool,
        Field(default=True, description="是否必修（True=必修, False=选修）"),
    ] = True,
    type_name: Annotated[
        str,
        Field(default="", description='小节类型标签，如"考试"、"测验"'),
    ] = "",
    tags: Annotated[
        list[str] | None,
        Field(default=None, description="标签文本列表"),
    ] = None,
    sort_order: Annotated[
        int,
        Field(default=0, description="排序序号，0 表示自动追加到末尾"),
    ] = 0,
    accept_submission_time: Annotated[
        int,
        Field(default=0, description="开始接受提交时间（Unix时间戳，0=不限制）。例如 2026-06-09 09:00:00 对应 1780966800"),
    ] = 0,
    refuse_submission_time: Annotated[
        int,
        Field(default=0, description="截止提交时间（Unix时间戳，0=不限制）。例如 2026-06-10 10:00:00 对应 1781056800"),
    ] = 0,
    question_show_mode: Annotated[
        str,
        Field(default="0", description='展示样式: "0"=一页式(默认), "1"=逐题式'),
    ] = "0",
    allow_answer_type: Annotated[
        str,
        Field(default="1", description='开放式问题提交格式: "1"=文字+图片(默认), "0"=仅文字'),
    ] = "1",
    exam_result_setting: Annotated[
        str,
        Field(default="0", description='成绩设置: "0"=最后一次提交为准(默认)'),
    ] = "0",
    switch_window_limit: Annotated[
        int,
        Field(default=0, ge=0, description="防切屏次数，0=不设置(默认)"),
    ] = 0,
    quiz_completion_condition: Annotated[
        str,
        Field(default="0", description='完成条件: "0"=不设置(默认)'),
    ] = "0",
    share_status: Annotated[
        int,
        Field(default=1, ge=1, le=3, description="访问权限: 1=课程内公开(默认), 2=企业内公开, 3=仅自己"),
    ] = 1,
    submit_permission: Annotated[
        int,
        Field(default=1, description="提交权限: 1=课程内学员(默认)"),
    ] = 1,
    show_answer_after_submit: Annotated[
        bool,
        Field(default=False, description="提交后展示正确答案，False=不展示(默认), True=展示"),
    ] = False,
    allow_add_question_collection: Annotated[
        bool,
        Field(default=True, description="允许将题目加入考题本，True=允许(默认), False=不允许"),
    ] = True,
    is_show_quiz_ranking: Annotated[
        bool,
        Field(default=True, description="提交后展示考试排行榜，True=展示(默认), False=不展示"),
    ] = True,
    is_answer_paste: Annotated[
        bool,
        Field(default=True, description="回答开放式问题是否允许粘贴，True=允许(默认), False=不允许"),
    ] = True,
    quiz_cover_tips_type: Annotated[
        str,
        Field(default="1", description='封面提示类型: "1"=自动设置(默认), "0"=手动设置'),
    ] = "1",
    quiz_cover_tips_content: Annotated[
        str,
        Field(default="", description='封面提示内容。quiz_cover_tips_type="1"时为空则系统自动生成'),
    ] = "",
    point_ratio: Annotated[
        int,
        Field(default=1, ge=0, description="小节基本积分倍率，默认 1"),
    ] = 1,
    is_set_quiz_cover: Annotated[
        bool,
        Field(default=True, description="是否设置考试封面，True=设置(默认)，False=不设置"),
    ] = True,
    jump_button: Annotated[
        bool,
        Field(default=False, description="提交成功页是否显示跳转按钮，False=不跳转(默认)，True=显示"),
    ] = False,
    jump_url: Annotated[
        str,
        Field(default="", description="跳转按钮的目标 URL（jump_button=True 时有效）"),
    ] = "",
    jump_button_title: Annotated[
        str,
        Field(default="", description="跳转按钮的文本（jump_button=True 时有效）"),
    ] = "",
    result_prompt: Annotated[
        str,
        Field(default="", description="提交成功提示语。空字符串时使用系统默认提示"),
    ] = "",
    show_user_result_mode: Annotated[
        str | None,
        Field(
            default=None,
            description='提交后展示内容模式。None 时使用 show_user_result 布尔值。'
                        '"0"=已提交答案, "1"=正确答案, "2"=不展示答案, "3"=展示对错不展示答案',
        ),
    ] = None,
    display_score: Annotated[
        bool,
        Field(default=True, description="是否向学员展示考试分数，True=展示(默认), False=不展示"),
    ] = True,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """在课程中创建考试类型小节.

    触发条件：需要向课程中添加考试/测验小节时调用。
    前置依赖：需先调用 tch_login 完成登录，已有 group_id。

    考试小节支持 3 种题目类型：单选题、多选题、开放题。
    题目通过 questions_json 参数传入，为 JSON 字符串格式。

    多选题得分规则：
    - scoring_rule="all_correct"（默认）：全部正确才得分，否则不得分
    - scoring_rule="partial"：全部正确得满分，少选得 partial_score 分，错选/多选/不选不得分

    开放题标准答案：
    - 设置 standard_answers 时，学员提交与任一标准答案一致则自动得分
    - 不设置 standard_answers 时，学员提交后需 teacher 手动评分

    考试设置参数说明：
    - 提交时间：accept_submission_time / refuse_submission_time（Unix时间戳）
    - 展示样式：question_show_mode（"0"=一页式, "1"=单题卡片式）
    - 考试成绩：exam_result_setting（"0"=最后一次提交为准, "1"=以最高分为准）
    - 完成条件：quiz_completion_condition（"0"=不设置, "1"=考试成绩达到及格分）
    - 访问权限：share_status（1=课程内公开, 2=企业内公开, 3=仅自己）
    - 封面提示：quiz_cover_tips_type="1"（自动）时系统根据题目自动生成
    - 跳转按钮：jump_button=True 时可设置 jump_url 和 jump_button_title
    - 积分倍率：point_ratio（小节基本积分 = 基本分10 × point_ratio）

    返回：包含 session_id、题目数量、总分值的 JSON
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 tch_login 完成登录后再重试",
        )

    try:
        # 解析题目 JSON
        questions = json.loads(questions_json)
        if not isinstance(questions, list):
            raise ValueError("questions_json 必须解析为列表")

        builder = CourseBuilder(client)
        result = builder.create_exam_section(
            group_id=group_id,
            session_title=session_title,
            questions=questions,
            description=description,
            exam_duration_seconds=exam_duration_minutes * 60,
            quiz_count_limit=quiz_count_limit,
            quiz_pass_mark=quiz_pass_mark,
            random_option=random_option,
            show_user_result=show_user_result,
            submit_one_by_one=submit_one_by_one,
            accept_submission_time=accept_submission_time,
            refuse_submission_time=refuse_submission_time,
            is_required=is_required,
            type_name=type_name,
            tags=tags,
            sort_order=sort_order,
            question_show_mode=question_show_mode,
            allow_answer_type=allow_answer_type,
            exam_result_setting=exam_result_setting,
            switch_window_limit=switch_window_limit,
            quiz_completion_condition=quiz_completion_condition,
            share_status=share_status,
            submit_permission=submit_permission,
            show_answer_after_submit=show_answer_after_submit,
            allow_add_question_collection=allow_add_question_collection,
            is_show_quiz_ranking=is_show_quiz_ranking,
            is_answer_paste=is_answer_paste,
            quiz_cover_tips_type=quiz_cover_tips_type,
            quiz_cover_tips_content=quiz_cover_tips_content,
            point_ratio=point_ratio,
            is_set_quiz_cover=is_set_quiz_cover,
            jump_button=jump_button,
            jump_url=jump_url,
            jump_button_title=jump_button_title,
            result_prompt=result_prompt,
            show_user_result_mode=show_user_result_mode,
            display_score=display_score,
        )

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="考试小节创建成功。如需继续添加更多小节，可再次调用；"
                            "如需查看课程详情，调用 tch_get_course_detail。",
        )
    except json.JSONDecodeError as e:
        return _err(
            error_code="INVALID_QUESTIONS_JSON",
            error_message=f"questions_json 解析失败: {e}",
            suggested_action="请检查 questions_json 是否为有效的 JSON 格式。",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_PARAMS",
            error_message=str(e),
            suggested_action="请检查题目格式是否符合要求（如单选题必须提供 options 和 correct_indices）",
        )
    except Exception as e:
        logger.exception("创建考试小节异常")
        return _err(
            error_code="CREATE_EXAM_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def tch_update_exam_section(
    group_id: Annotated[str, Field(description="课程 ID")],
    session_id: Annotated[str, Field(description="考试小节 ID")],
    session_title: Annotated[
        str | None,
        Field(default=None, description="新的小节标题，None 表示不修改"),
    ] = None,
    questions_json: Annotated[
        str | None,
        Field(
            default=None,
            description='题目列表的 JSON 字符串。None 表示不修改题目。格式与创建时相同。'
                        '**单选题**：{"type":"radio","title":"题目","score":5,"options":["A","B","C"],"correct_indices":[2]}\n'
                        '**多选题**：{"type":"checkbox","title":"题目","score":7,"options":["A","B","C","D"],"correct_indices":[0,1,2],"scoring_rule":"all_correct"}\n'
                        '**开放题**：{"type":"input","title":"题目","score":10,"standard_answers":["答案1"]}',
        ),
    ] = None,
    description: Annotated[
        str | None,
        Field(default=None, description="考试说明/描述，None 表示不修改"),
    ] = None,
    exam_duration_minutes: Annotated[
        int | None,
        Field(default=None, ge=0, description="考试时长（分钟），0=不限时，None 表示不修改"),
    ] = None,
    quiz_count_limit: Annotated[
        int | None,
        Field(default=None, ge=0, description="考试次数限制，0=不限次数，None 表示不修改"),
    ] = None,
    quiz_pass_mark: Annotated[
        int | None,
        Field(default=None, ge=0, le=100, description="及格线（百分比 0-100），0=不设及格线，None 表示不修改"),
    ] = None,
    random_option: Annotated[
        bool | None,
        Field(default=None, description="是否随机展示选项顺序，None 表示不修改"),
    ] = None,
    show_user_result: Annotated[
        bool | None,
        Field(default=None, description="是否向学员展示成绩，None 表示不修改"),
    ] = None,
    submit_one_by_one: Annotated[
        bool | None,
        Field(default=None, description="是否逐题提交，None 表示不修改"),
    ] = None,
    is_required: Annotated[
        bool | None,
        Field(default=None, description="是否必修（True=必修, False=选修），None 表示不修改"),
    ] = None,
    type_name: Annotated[
        str | None,
        Field(default=None, description='小节类型标签，None 表示不修改'),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Field(default=None, description="标签文本列表，None 表示不修改"),
    ] = None,
    accept_submission_time: Annotated[
        int | None,
        Field(default=None, description="开始接受提交时间（Unix时间戳，0=不限制），None 表示不修改"),
    ] = None,
    refuse_submission_time: Annotated[
        int | None,
        Field(default=None, description="截止提交时间（Unix时间戳，0=不限制），None 表示不修改"),
    ] = None,
    question_show_mode: Annotated[
        str | None,
        Field(default=None, description='展示样式: "0"=一页式, "1"=逐题式，None 表示不修改'),
    ] = None,
    allow_answer_type: Annotated[
        str | None,
        Field(default=None, description='开放式问题提交格式: "1"=文字+图片, "0"=仅文字，None 表示不修改'),
    ] = None,
    exam_result_setting: Annotated[
        str | None,
        Field(default=None, description='成绩设置: "0"=最后一次提交为准, "1"=以最高分为准，None 表示不修改'),
    ] = None,
    switch_window_limit: Annotated[
        int | None,
        Field(default=None, ge=0, description="防切屏次数，0=不设置，None 表示不修改"),
    ] = None,
    quiz_completion_condition: Annotated[
        str | None,
        Field(default=None, description='完成条件: "0"=不设置, "1"=考试成绩达到及格分，None 表示不修改'),
    ] = None,
    share_status: Annotated[
        int | None,
        Field(default=None, ge=0, le=3, description="访问权限: 1=课程内公开, 2=企业内公开, 0=关闭，None 表示不修改"),
    ] = None,
    submit_permission: Annotated[
        int | None,
        Field(default=None, description="提交权限: 1=课程内学员，None 表示不修改"),
    ] = None,
    show_answer_after_submit: Annotated[
        bool | None,
        Field(default=None, description="提交后展示正确答案，None 表示不修改"),
    ] = None,
    allow_add_question_collection: Annotated[
        bool | None,
        Field(default=None, description="允许将题目加入考题本，None 表示不修改"),
    ] = None,
    is_show_quiz_ranking: Annotated[
        bool | None,
        Field(default=None, description="提交后展示考试排行榜，None 表示不修改"),
    ] = None,
    is_answer_paste: Annotated[
        bool | None,
        Field(default=None, description="回答开放式问题是否允许粘贴，None 表示不修改"),
    ] = None,
    quiz_cover_tips_type: Annotated[
        str | None,
        Field(default=None, description='封面提示类型: "1"=自动设置, "0"=手动设置，None 表示不修改'),
    ] = None,
    quiz_cover_tips_content: Annotated[
        str | None,
        Field(default=None, description="封面提示内容，None 表示不修改"),
    ] = None,
    point_ratio: Annotated[
        int | None,
        Field(default=None, ge=0, description="小节基本积分倍率，None 表示不修改"),
    ] = None,
    is_set_quiz_cover: Annotated[
        bool | None,
        Field(default=None, description="是否设置考试封面，None 表示不修改"),
    ] = None,
    jump_button: Annotated[
        bool | None,
        Field(default=None, description="提交成功页是否显示跳转按钮，None 表示不修改"),
    ] = None,
    jump_url: Annotated[
        str | None,
        Field(default=None, description="跳转按钮的目标 URL，None 表示不修改"),
    ] = None,
    jump_button_title: Annotated[
        str | None,
        Field(default=None, description="跳转按钮的文本，None 表示不修改"),
    ] = None,
    result_prompt: Annotated[
        str | None,
        Field(default=None, description="提交成功提示语，None 表示不修改"),
    ] = None,
    show_user_result_mode: Annotated[
        str | None,
        Field(
            default=None,
            description='提交后展示内容模式。'
                        '"0"=已提交答案, "1"=正确答案, "2"=不展示答案, "3"=展示对错不展示答案。'
                        'None 表示不修改',
        ),
    ] = None,
    display_score: Annotated[
        bool | None,
        Field(default=None, description="是否向学员展示考试分数，None 表示不修改"),
    ] = None,
    sid: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """编辑课程中的考试类型小节.

    触发条件：需要修改已有考试小节的设置（如考试时长、及格线、访问权限等）时调用。
    前置依赖：需先调用 tch_login 完成登录，已有 group_id 和 session_id。

    更新规则：
    - 所有参数均为可选，传入 None 表示不修改该字段
    - 如需修改题目，提供完整的 questions_json；None 表示保留原有题目
    - 修改设置后立即生效

    返回：包含 session_id 和实际变更字段列表的 JSON
    """
    client = _get_client(sid)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 tch_login 完成登录后再重试",
        )

    try:
        questions = None
        if questions_json is not None:
            questions = json.loads(questions_json)
            if not isinstance(questions, list):
                raise ValueError("questions_json 必须解析为列表")

        builder = CourseBuilder(client)
        result = builder.update_exam_section(
            group_id=group_id,
            session_id=session_id,
            session_title=session_title,
            questions=questions,
            description=description,
            exam_duration_seconds=exam_duration_minutes * 60 if exam_duration_minutes is not None else None,
            quiz_count_limit=quiz_count_limit,
            quiz_pass_mark=quiz_pass_mark,
            random_option=random_option,
            show_user_result=show_user_result,
            submit_one_by_one=submit_one_by_one,
            accept_submission_time=accept_submission_time,
            refuse_submission_time=refuse_submission_time,
            is_required=is_required,
            type_name=type_name,
            tags=tags,
            question_show_mode=question_show_mode,
            allow_answer_type=allow_answer_type,
            exam_result_setting=exam_result_setting,
            switch_window_limit=switch_window_limit,
            quiz_completion_condition=quiz_completion_condition,
            share_status=share_status,
            submit_permission=submit_permission,
            show_answer_after_submit=show_answer_after_submit,
            allow_add_question_collection=allow_add_question_collection,
            is_show_quiz_ranking=is_show_quiz_ranking,
            is_answer_paste=is_answer_paste,
            quiz_cover_tips_type=quiz_cover_tips_type,
            quiz_cover_tips_content=quiz_cover_tips_content,
            point_ratio=point_ratio,
            is_set_quiz_cover=is_set_quiz_cover,
            jump_button=jump_button,
            jump_url=jump_url,
            jump_button_title=jump_button_title,
            result_prompt=result_prompt,
            show_user_result_mode=show_user_result_mode,
            display_score=display_score,
        )

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="考试小节更新成功。如需查看更新后内容，调用 tch_get_course_detail。",
        )
    except json.JSONDecodeError as e:
        return _err(
            error_code="INVALID_QUESTIONS_JSON",
            error_message=f"questions_json 解析失败: {e}",
            suggested_action="请检查 questions_json 是否为有效的 JSON 格式。",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_PARAMS",
            error_message=str(e),
            suggested_action="请检查题目格式是否符合要求（如单选题必须提供 options 和 correct_indices）",
        )
    except Exception as e:
        logger.exception("更新考试小节异常")
        return _err(
            error_code="UPDATE_EXAM_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接、session_id 是否存在、以及参数后重试",
        )


@mcp.tool()
async def tch_create_signin_section(
    group_id: Annotated[str, Field(description="课程 ID，来自 tch_create_course")],
    session_title: Annotated[str, Field(description="签到小节标题")],
    signin_info_json: Annotated[
        str,
        Field(
            description='签到信息（问题）列表的 JSON 字符串。每项为一个信息对象，支持 5 种类型。\n'
                        '**文本输入**：{"type":"textarea","title":"您的姓名是？","required":true,"hint":"请输入姓名"}\n'
                        '  - hint: 占位提示文字（可选，默认空）\n'
                        '**单选题**：{"type":"radio","title":"您的性别是？","required":true,"options":["女","男"]}\n'
                        '**多选题**：{"type":"checkbox","title":"谁是你的朋友？","required":true,"options":["黄飞鸿","洪七公","周伯通"],"min_options":1,"max_options":2}\n'
                        '  - min_options: 最少选几项（可选，默认1）\n'
                        '  - max_options: 最多选几项（可选，默认等于选项数）\n'
                        '**数值题**：{"type":"number","title":"您的工作年限是？","required":false,"min":0,"max":50,"default":0}\n'
                        '  - min: 最小值（可选，默认0）；max: 最大值（可选，默认100）；default: 默认值（可选，默认等于min）\n'
                        '**段落说明**：{"type":"paragraph","content":"<p>这是一段说明文字</p>"}\n'
                        '  - content: 支持 HTML 格式'
        ),
    ],
    auto_check: Annotated[
        bool,
        Field(default=True, description="是否自动审核签到（True=自动审核(默认), False=手动审核）"),
    ] = True,
    is_required: Annotated[
        bool,
        Field(default=True, description="是否必修（True=必修(默认), False=选修）"),
    ] = True,
    point_ratio: Annotated[
        int,
        Field(default=1, ge=0, description="小节基本积分倍率，默认 1"),
    ] = 1,
    is_anti_fraud: Annotated[
        bool,
        Field(default=False, description="是否开启防作弊（True=开启, False=关闭(默认)）"),
    ] = False,
    mini_program_switch: Annotated[
        bool,
        Field(default=True, description="是否开启小程序（True=开启(默认), False=关闭）"),
    ] = True,
    share_status: Annotated[
        int,
        Field(default=1, ge=1, le=3, description="访问权限: 1=课程内公开(默认), 2=企业内公开, 3=仅自己"),
    ] = 1,
    result_prompt: Annotated[
        str,
        Field(default="", description="签到成功提示语。空字符串时使用系统默认提示"),
    ] = "",
    type_name: Annotated[
        str,
        Field(default="", description='小节类型标签，如"签到"'),
    ] = "",
    desc_richtext: Annotated[
        str,
        Field(default="", description="富文本签到说明（HTML格式）。支持图片、链接、格式化文字"),
    ] = "",
    tags: Annotated[
        list[str] | None,
        Field(default=None, description="标签文本列表"),
    ] = None,
    sort_order: Annotated[
        int,
        Field(default=0, description="排序序号，0 表示自动追加到末尾"),
    ] = 0,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """在课程中创建签到类型小节.

    触发条件：需要向课程中添加签到小节时调用。
    前置依赖：需先调用 tch_login 完成登录，已有 group_id。

    签到小节(sessionType=6)允许学员在签到时填写各种信息，支持文本输入、
    单选、多选、数值题、段落说明五种类型。

    签到设置参数说明：
    - auto_check: True=自动审核(默认)，学员签到后立即完成
    - is_anti_fraud: True=开启防作弊检测
    - mini_program_switch: True=小程序端可签到(默认)
    - share_status: 1=课程内公开(默认), 2=企业内公开, 3=仅自己
    - result_prompt: 学员签到成功后的提示语，如"签到成功！"
    - point_ratio: 小节基本积分 = 基本分10 × point_ratio

    返回：包含 session_id、签到信息数量、multimedia_id 的 JSON
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 tch_login 完成登录后再重试",
        )

    try:
        signin_info_list = json.loads(signin_info_json)
        if not isinstance(signin_info_list, list):
            raise ValueError("signin_info_json 必须解析为列表")
        if not signin_info_list:
            raise ValueError("signin_info_list 不能为空，签到小节至少需要包含一个签到信息")

        builder = CourseBuilder(client)
        result = builder.create_signin_section(
            group_id=group_id,
            session_title=session_title,
            signin_info_list=signin_info_list,
            auto_check=auto_check,
            is_required=is_required,
            point_ratio=point_ratio,
            is_anti_fraud=is_anti_fraud,
            mini_program_switch=mini_program_switch,
            share_status=share_status,
            result_prompt=result_prompt,
            type_name=type_name,
            desc_richtext=desc_richtext,
            tags=tags,
            sort_order=sort_order,
        )

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="签到小节创建成功。如需继续添加更多小节，可再次调用；"
                            "如需查看课程详情，调用 tch_get_course_detail。",
        )
    except json.JSONDecodeError as e:
        return _err(
            error_code="INVALID_SIGNIN_INFO_JSON",
            error_message=f"signin_info_json 解析失败: {e}",
            suggested_action="请检查 signin_info_json 是否为有效的 JSON 格式。",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_PARAMS",
            error_message=str(e),
            suggested_action="请检查签到信息格式是否符合要求（如单选题必须提供 options）",
        )
    except Exception as e:
        logger.exception("创建签到小节异常")
        return _err(
            error_code="CREATE_SIGNIN_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def tch_update_signin_section(
    group_id: Annotated[str, Field(description="课程 ID")],
    session_id: Annotated[str, Field(description="签到小节 ID")],
    session_title: Annotated[
        str | None,
        Field(default=None, description="新的小节标题，None 表示不修改"),
    ] = None,
    signin_info_json: Annotated[
        str | None,
        Field(
            default=None,
            description='新的签到信息列表 JSON 字符串（完整列表，按目标顺序排列）。'
                        'None 表示不修改签到信息。格式与创建时相同。\n'
                        '按索引位置与现有签到信息匹配更新，相同位置且类型相同则保留原有 ID。'
        ),
    ] = None,
    auto_check: Annotated[
        bool | None,
        Field(default=None, description="是否自动审核，None 表示不修改"),
    ] = None,
    is_required: Annotated[
        bool | None,
        Field(default=None, description="是否必修，None 表示不修改"),
    ] = None,
    point_ratio: Annotated[
        int | None,
        Field(default=None, ge=0, description="积分倍率，None 表示不修改"),
    ] = None,
    is_anti_fraud: Annotated[
        bool | None,
        Field(default=None, description="是否开启防作弊，None 表示不修改"),
    ] = None,
    mini_program_switch: Annotated[
        bool | None,
        Field(default=None, description="是否开启小程序，None 表示不修改"),
    ] = None,
    share_status: Annotated[
        int | None,
        Field(default=None, ge=1, le=3, description="访问权限，None 表示不修改"),
    ] = None,
    result_prompt: Annotated[
        str | None,
        Field(default=None, description="签到成功提示语，None 表示不修改"),
    ] = None,
    type_name: Annotated[
        str | None,
        Field(default=None, description="小节类型标签，None 表示不修改"),
    ] = None,
    desc_richtext: Annotated[
        str | None,
        Field(
            default=None,
            description="富文本签到说明（HTML）。None 表示不修改，空字符串表示清除",
        ),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Field(default=None, description="标签列表，None 表示不修改"),
    ] = None,
    sid: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """编辑课程中的签到类型小节.

    触发条件：需要修改已有签到小节的设置或签到信息时调用。
    前置依赖：需先调用 tch_login 完成登录，已有 group_id 和 session_id。

    更新规则：
    - 所有参数均为可选，传入 None 表示不修改该字段
    - 如需修改签到信息，提供完整的 signin_info_json；None 表示保留原有信息
    - 修改设置后立即生效

    签到信息匹配规则：
    - signin_info_json 按索引位置与现有 sectionArr 匹配
    - 相同位置且类型相同 → 保留原有 questionId/answerId，更新其他字段
    - 类型不同 → 旧信息删除，新信息新增
    - 新数组长度超过旧数组 → 超出部分为新增
    - 新数组长度短于旧数组 → 缺少部分保留原信息

    返回：包含 session_id 和实际变更字段列表的 JSON
    """
    client = _get_client(sid)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 tch_login 完成登录后再重试",
        )

    try:
        signin_info_list = None
        if signin_info_json is not None:
            signin_info_list = json.loads(signin_info_json)
            if not isinstance(signin_info_list, list):
                raise ValueError("signin_info_json 必须解析为列表")
            if not signin_info_list:
                raise ValueError("signin_info_list 不能为空")

        builder = CourseBuilder(client)
        result = builder.update_signin_section(
            group_id=group_id,
            session_id=session_id,
            session_title=session_title,
            signin_info_list=signin_info_list,
            auto_check=auto_check,
            is_required=is_required,
            point_ratio=point_ratio,
            is_anti_fraud=is_anti_fraud,
            mini_program_switch=mini_program_switch,
            share_status=share_status,
            result_prompt=result_prompt,
            type_name=type_name,
            desc_richtext=desc_richtext,
            tags=tags,
        )

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="签到小节更新成功。如需查看更新后内容，调用 tch_get_course_detail。",
        )
    except json.JSONDecodeError as e:
        return _err(
            error_code="INVALID_SIGNIN_INFO_JSON",
            error_message=f"signin_info_json 解析失败: {e}",
            suggested_action="请检查 signin_info_json 是否为有效的 JSON 格式。",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_PARAMS",
            error_message=str(e),
            suggested_action="请检查签到信息格式是否符合要求（如单选题必须提供 options）",
        )
    except Exception as e:
        logger.exception("更新签到小节异常")
        return _err(
            error_code="UPDATE_SIGNIN_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接、session_id 是否存在、以及参数后重试",
        )


@mcp.tool()
async def tch_update_survey_section(
    group_id: Annotated[str, Field(description="课程 ID，来自 tch_create_course")],
    session_id: Annotated[str, Field(description="问卷小节 ID")],
    questions_json: Annotated[
        str,
        Field(
            description='题目列表的 JSON 字符串。每项为一个题目对象，支持 5 种类型。'
                        '按目标顺序排列，会与现有题目按索引位置匹配更新。\n'
                        '**单选题**：{"type":"radio","title":"题目","required":true,"options":["A","B"]}\n'
                        '**多选题**：{"type":"checkbox","title":"题目","required":true,"options":["A","B"],"min_options":1,"max_options":2}\n'
                        '**填空题**：{"type":"textarea","title":"题目","required":true}\n'
                        '**量值题**：{"type":"number","title":"评分","required":true,"min_value":1,"max_value":5,"min_label":"差","max_label":"好"}\n'
                        '**段落说明**：{"type":"paragraph","content":"<p>说明文字</p>"} — 可放在任意位置\n'
                        '单选/多选支持 extra_answer: {"label":"其他","required":false}'
        ),
    ],
    session_title: Annotated[
        str | None,
        Field(default=None, description="新的小节标题，None 表示不修改"),
    ] = None,
    session_desc: Annotated[
        str | None,
        Field(
            default=None,
            description="小节描述/问卷说明（学员进入问卷时展示的富文本说明），None 表示不修改",
        ),
    ] = None,
    is_required: Annotated[
        bool | None,
        Field(default=None, description="是否必修（True=必修, False=选修），None 表示不修改"),
    ] = None,
    jump_button: Annotated[
        bool | None,
        Field(default=None, description="提交成功后是否显示跳转按钮，None 表示不修改"),
    ] = None,
    jump_url: Annotated[
        str | None,
        Field(default=None, description="跳转按钮的目标 URL，None 表示不修改"),
    ] = None,
    jump_button_title: Annotated[
        str | None,
        Field(default=None, description="跳转按钮的文本，None 表示不修改"),
    ] = None,
    show_user_result: Annotated[
        bool | None,
        Field(default=None, description="提交后是否展示问卷结果，None 表示不修改"),
    ] = None,
    is_show_participate_on_screen: Annotated[
        bool | None,
        Field(default=None, description="大屏幕是否展示参与人数，None 表示不修改"),
    ] = None,
    share_status: Annotated[
        int | None,
        Field(default=None, description="问卷访问权限: 1=课程内公开, 2=企业内公开, 3=仅自己/关闭，None 表示不修改"),
    ] = None,
    submit_permission: Annotated[
        int | None,
        Field(default=None, description="提交权限: 3=不允许匿名/必须登录, 4=允许匿名提交，None 表示不修改"),
    ] = None,
    allow_modify: Annotated[
        bool | None,
        Field(default=None, description="是否允许提交后修改问卷，None 表示不修改"),
    ] = None,
    submit_limit: Annotated[
        int | str | None,
        Field(default=None, description='提交次数限制: 1=最多1次, 0=不限, n=允许多次，None 表示不修改'),
    ] = None,
    result_prompt: Annotated[
        str | None,
        Field(default=None, description="提交成功提示语，None 表示不修改"),
    ] = None,
    accept_submission_time: Annotated[
        int | str | None,
        Field(default=None, description='开始提交时间。支持格式: Unix时间戳整数、"YYYY-MM-DD HH:MM"或"YYYY/MM/DD HH:MM"。0=不限制，None 表示不修改'),
    ] = None,
    refuse_submission_time: Annotated[
        int | str | None,
        Field(default=None, description='结束提交时间。支持格式: Unix时间戳整数、"YYYY-MM-DD HH:MM"或"YYYY/MM/DD HH:MM"。0=不限制，None 表示不修改'),
    ] = None,
    random_option: Annotated[
        bool | None,
        Field(default=None, description="选项是否随机展示，None 表示不修改"),
    ] = None,
    type_name: Annotated[
        str | None,
        Field(default=None, description='小节类型标签，如"问卷"、"调研"，None 表示不修改'),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Field(default=None, description="标签文本列表，None 表示不修改"),
    ] = None,
    sid: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """编辑课程中的问卷类型小节.

    触发条件：需要修改已有问卷小节的内容、规则、题目顺序时调用。
    前置依赖：需先调用 tch_login 完成登录，已有 group_id 和 session_id。

    更新规则：
    - questions_json 必须提供完整的目标题目列表，按最终顺序排列
    - 相同位置且 domType 相同 → 保留原有 ID，更新字段
    - domType 不同 → 删除旧题目，新增新题目
    - 新数组比旧数组长 → 超出部分为新增题目
    - 新数组比旧数组短 → 缺少部分调用 /ajax/e_deleteQuestion 真正删除

    返回：包含 session_id、实际变更字段列表、被删除题目 ID 列表的 JSON
    """
    client = _get_client(sid)

    try:
        questions = json.loads(questions_json)
        if not isinstance(questions, list):
            raise ValueError("questions_json 必须解析为列表")

        builder = CourseBuilder(client)
        result = builder.update_survey_section(
            group_id=group_id,
            session_id=session_id,
            questions=questions,
            session_title=session_title,
            desc=session_desc,
            is_required=is_required,
            jump_button=jump_button,
            jump_url=jump_url,
            jump_button_title=jump_button_title,
            show_user_result=show_user_result,
            is_show_participate_on_screen=is_show_participate_on_screen,
            share_status=share_status,
            submit_permission=submit_permission,
            allow_modify=allow_modify,
            submit_limit=submit_limit,
            result_prompt=result_prompt,
            accept_submission_time=accept_submission_time,
            refuse_submission_time=refuse_submission_time,
            random_option=random_option,
            type_name=type_name,
            tags=tags,
        )

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="问卷小节更新成功。如需查看更新后内容，调用 tch_get_course_detail。",
        )
    except json.JSONDecodeError as e:
        return _err(
            error_code="INVALID_QUESTIONS_JSON",
            error_message=f"questions_json 解析失败: {e}",
            suggested_action="请检查 questions_json 是否为有效的 JSON 格式。",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_PARAMS",
            error_message=str(e),
            suggested_action="请检查题目格式是否符合要求（如单选题必须提供 options）",
        )
    except Exception as e:
        logger.exception("更新问卷小节异常")
        return _err(
            error_code="UPDATE_SURVEY_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接、session_id 是否存在、以及参数后重试",
        )


@mcp.tool()
async def tch_update_document_section(
    group_id: Annotated[str, Field(description="课程 ID，要修改的课程")],
    section_id: Annotated[
        str,
        Field(description="小节 ID（即 savesession 返回的 session_id），要修改的现有小节"),
    ],
    section_title: Annotated[
        str | None,
        Field(default=None, description="新的小节标题，不传则保持原值"),
    ] = None,
    document_resource_id: Annotated[
        str | None,
        Field(
            default=None,
            description="新的文档资源 ID（已有资源）。与 document_file_path 二选一，不传则保持原资源",
        ),
    ] = None,
    document_file_path: Annotated[
        str | None,
        Field(
            default=None,
            description="新的本地文档文件路径。与 document_resource_id 二选一，不传则保持原资源",
        ),
    ] = None,
    desc_plain: Annotated[
        str | None,
        Field(default=None, description="新的纯文本文档说明。不传则保持原值"),
    ] = None,
    desc_richtext: Annotated[
        str | None,
        Field(default=None, description="新的富文本文档说明（HTML 格式）。不传则保持原值"),
    ] = None,
    section_cover_path: Annotated[
        str | None,
        Field(default=None, description="新的小节封面图片路径（jpg/png），不传则保持原封面"),
    ] = None,
    is_required: Annotated[
        bool | None,
        Field(default=None, description="是否必修（True=必修, False=选修），不传则保持原值"),
    ] = None,
    allow_download: Annotated[
        bool | None,
        Field(default=None, description="是否允许学员下载文档，不传则保持原值"),
    ] = None,
    min_duration_minutes: Annotated[
        int | None,
        Field(default=None, description="最小学习时长（分钟），0=不限制，不传则保持原值"),
    ] = None,
    finish_condition: Annotated[
        str | None,
        Field(
            default=None,
            description='完成条件: "open"=打开即完成, "last_page"=学完最后一页，不传则保持原值',
        ),
    ] = None,
    show_creator_info: Annotated[
        bool | None,
        Field(default=None, description="是否展示课程创建者信息，不传则保持原值"),
    ] = None,
    enable_comment: Annotated[
        bool | None,
        Field(default=None, description="是否开启发言区，不传则保持原值"),
    ] = None,
    show_comment_time: Annotated[
        bool | None,
        Field(default=None, description="是否显示发言提交时间，不传则保持原值"),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Field(default=None, description="新的标签文本列表，不传则保持原值"),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """修改课程中已有的文档类型小节.

    触发条件：需要修改文档小节的标题、文档资源、说明、权限、时长、完成条件等参数时调用。
    前置依赖：需先调用 tch_login 完成登录，并已有 group_id 和 section_id。

    重要特性：部分更新 —— 只修改传入的字段，未传入的字段保持原值不变。
    例如只传 section_title 则只改标题，其他所有参数保持原样。

    支持的部分更新（至少传入一个变更字段）：
    - 更换文档资源（提供新的 document_resource_id 或 document_file_path）
    - 修改小节标题
    - 修改文档说明（纯文本 desc_plain 或富文本 desc_richtext）
    - 修改封面图
    - 切换必修/选修状态
    - 调整下载权限
    - 调整学习时长
    - 调整完成条件
    - 调整展示创建者信息
    - 调整发言区设置
    - 调整标签

    文档说明注意事项：
    - 传入 desc_plain 会将说明切换为纯文本模式（清除已有的富文本说明）
    - 传入 desc_richtext 会将说明切换为富文本模式（创建新的富文本内容）
    - desc_plain 和 desc_richtext 不能同时提供
    - 如果不修改说明，两者都不传，保持原说明不变

    参数单位提醒：
    - min_duration_minutes 的单位是分钟，但内部会转换为秒（×60）存储
    - finish_condition 只有两种取值："open"（打开即完成）或 "last_page"（学完最后一页）

    返回：包含 session_id 和 changes（变更字段列表）的 JSON
    """
    client = _get_client(session_id)

    # 校验至少提供一个变更字段
    if all(
        v is None
        for v in [
            section_title,
            document_resource_id,
            document_file_path,
            desc_plain,
            desc_richtext,
            section_cover_path,
            is_required,
            allow_download,
            min_duration_minutes,
            finish_condition,
            show_creator_info,
            enable_comment,
            show_comment_time,
            tags,
        ]
    ):
        return _err(
            error_code="NO_CHANGES",
            error_message="未提供任何要修改的字段",
            suggested_action="至少传入一个要修改的参数，如 section_title / allow_download / min_duration_minutes 等",
        )

    # 校验 resource 参数互斥
    if document_resource_id and document_file_path:
        return _err(
            error_code="MUTUALLY_EXCLUSIVE",
            error_message="document_resource_id 和 document_file_path 不能同时提供",
            suggested_action="二选一：使用已有资源 ID 或上传新文件",
        )

    # 校验 desc 参数互斥
    if desc_plain and desc_richtext:
        return _err(
            error_code="MUTUALLY_EXCLUSIVE",
            error_message="desc_plain 和 desc_richtext 不能同时提供",
            suggested_action="二选一：使用纯文本说明或富文本说明",
        )

    # 校验 finish_condition
    if finish_condition is not None and finish_condition not in ("open", "last_page"):
        return _err(
            error_code="INVALID_FINISH_CONDITION",
            error_message=f"finish_condition 必须是 'open' 或 'last_page'，收到: {finish_condition}",
            suggested_action="使用 'open'（打开即完成）或 'last_page'（学完最后一页）",
        )

    try:
        builder = CourseBuilder(client)

        # 1. 获取或上传文档资源
        actual_resource_id = None
        if document_resource_id or document_file_path:
            actual_resource_id, err = await _upload_document_if_needed(
                client, document_file_path, document_resource_id, section_title or "文档"
            )
            if err:
                return err

        # 2. 上传小节封面（如有）
        cover_resource_id, _ = _upload_image_if_needed(
            client, section_cover_path, media_type="picweike"
        )

        # 3. 调用 update_document_session
        result = builder.update_document_session(
            group_id=group_id,
            session_id=section_id,
            session_title=section_title,
            resource_id=actual_resource_id,
            cover_resource_id=cover_resource_id,
            desc_plain=desc_plain,
            desc_richtext=desc_richtext,
            is_required=is_required,
            allow_download=allow_download,
            min_duration_seconds=min_duration_minutes * 60 if min_duration_minutes is not None else None,
            finish_condition=finish_condition,
            show_creator_info=show_creator_info,
            enable_comment=enable_comment,
            show_comment_time=show_comment_time,
            tags=tags,
        )

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action=f"文档小节更新成功（变更字段: {', '.join(result['changes'])}）。"
                        f"建议下一步："
                        f"1) 调用 tch_get_section(section_id='{section_id}') 查看完整详情确认修改；"
                        f"2) 如需关闭学员可见性，调用 tch_toggle_section_visibility(section_id='{section_id}', visible=False)；"
                        f"3) 继续修改其他小节。",
        )

    except ValueError as e:
        logger.error("更新文档小节参数错误: %s", e)
        return _err(
            error_code="INVALID_PARAMS",
            error_message=str(e),
            suggested_action="检查参数格式是否符合要求",
        )
    except RuntimeError as e:
        logger.error("更新文档小节失败: %s", e)
        return _err(
            error_code="UPDATE_DOCUMENT_SECTION_FAILED",
            error_message=str(e),
            suggested_action="检查参数和文档文件后重试",
        )
    except Exception as e:
        logger.exception("更新文档小节异常")
        return _err(
            error_code="UPDATE_DOCUMENT_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def tch_toggle_section_visibility(
    section_id: Annotated[
        str,
        Field(description="小节 ID（即 savesession 返回的 session_id），要切换可见性的小节"),
    ],
    visible: Annotated[
        bool,
        Field(description="True=打开（学员可见，可学习）, False=关闭（学员不可见）"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """切换小节对学员的可见性（打开/关闭）.

    触发条件：需要临时隐藏或恢复显示课程中的某个小节时调用。
    前置依赖：需先调用 tch_login 完成登录，并已有 section_id。

    说明：
    - 打开（visible=True）：学员可以看到并学习该小节（默认状态）
    - 关闭（visible=False）：学员无法看到该小节，也无法学习
    - 关闭不会删除小节，只是对学员隐藏

    返回：包含操作结果和当前状态的 JSON
    """
    client = _get_client(session_id)

    try:
        builder = CourseBuilder(client)
        result = builder.toggle_session_visibility(
            session_id=section_id,
            visible=visible,
        )

        status_text = "打开" if visible else "关闭"
        if visible:
            toggle_hint = f"如需再次关闭，调用 tch_toggle_section_visibility(section_id='{section_id}', visible=False)"
        else:
            toggle_hint = f"如需重新打开，调用 tch_toggle_section_visibility(section_id='{section_id}', visible=True)"

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action=f"小节已{status_text}。学员现在{'可以' if visible else '无法'}看到并学习此小节。{toggle_hint}；如需查看详情，调用 tch_get_section(section_id='{section_id}')。",
        )

    except RuntimeError as e:
        logger.error("切换小节可见性失败: %s", e)
        return _err(
            error_code="TOGGLE_VISIBILITY_FAILED",
            error_message=str(e),
            suggested_action="请检查 section_id 是否正确后重试",
        )
    except Exception as e:
        logger.exception("切换小节可见性异常")
        return _err(
            error_code="TOGGLE_VISIBILITY_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def tch_update_scorm_section(
    group_id: Annotated[str, Field(description="课程 ID，要修改的课程")],
    section_id: Annotated[
        str,
        Field(description="小节 ID（即 savesession 返回的 session_id），要修改的现有小节"),
    ],
    section_title: Annotated[
        str | None,
        Field(default=None, description="新的小节标题，不传则保持原值"),
    ] = None,
    scorm_resource_id: Annotated[
        str | None,
        Field(
            default=None,
            description="新的 SCORM 资源 ID（已有资源）。与 scorm_file_path 二选一，不传则保持原资源",
        ),
    ] = None,
    scorm_file_path: Annotated[
        str | None,
        Field(
            default=None,
            description="新的本地 SCORM zip 文件路径。与 scorm_resource_id 二选一，不传则保持原资源",
        ),
    ] = None,
    section_cover_path: Annotated[
        str | None,
        Field(default=None, description="新的小节封面图片路径（jpg/png），不传则保持原封面"),
    ] = None,
    is_required: Annotated[
        bool | None,
        Field(default=None, description="是否必修（True=必修, False=选修），不传则保持原值"),
    ] = None,
    duration_minutes: Annotated[
        int | None,
        Field(default=None, description="预计学习时长（分钟），不传则保持原值"),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """修改课程中已有的 SCORM 小节.

    触发条件：需要更换 SCORM 资源、修改小节标题、或调整必修/选修状态时调用。
    前置依赖：需先调用 tch_login 完成登录，并已有 group_id 和 section_id。

    重要特性：部分更新 —— 只修改传入的字段，未传入的字段保持原值不变。
    例如只传 section_title 则只改标题，其他所有参数保持原样。

    支持的部分更新（至少传入一个变更字段）：
    - 更换 SCORM 资源（提供新的 scorm_resource_id 或 scorm_file_path）
    - 修改小节标题
    - 修改封面图
    - 切换必修/选修状态
    - 调整学习时长

    内部流程：
    1. 获取现有小节完整数据（含 sessionInfo 和所有服务器填充字段）
    2. 如有新 SCORM 文件，上传获取 resource_id
    3. 如有新封面，上传获取 cover_resource_id
    4. 应用变更到 sessionInfo（只修改传入的字段，其余保持原值）
    5. 调用 savesession 提交更新（含 session_id，触发更新模式）
    6. 如资源变更，调用 bind-upd 更新绑定（bind 新资源 + unbind 旧资源）

    返回：包含 session_id 和 changes（变更字段列表）的 JSON
    """
    client = _get_client(session_id)

    # 校验至少提供一个变更字段
    if all(
        v is None
        for v in [
            section_title,
            scorm_resource_id,
            scorm_file_path,
            section_cover_path,
            is_required,
            duration_minutes,
        ]
    ):
        return _err(
            error_code="NO_CHANGES",
            error_message="未提供任何要修改的字段",
            suggested_action="至少传入一个要修改的参数，如 section_title / scorm_resource_id / is_required 等",
        )

    # 校验 resource 参数互斥
    if scorm_resource_id and scorm_file_path:
        return _err(
            error_code="MUTUALLY_EXCLUSIVE",
            error_message="scorm_resource_id 和 scorm_file_path 不能同时提供",
            suggested_action="二选一：使用已有资源 ID 或上传新文件",
        )

    try:
        builder = CourseBuilder(client)

        # 1. 获取或上传 SCORM 资源
        actual_resource_id, err = await _upload_scorm_if_needed(
            client, scorm_file_path, scorm_resource_id, section_title or "SCORM"
        )
        if err:
            return err

        # 2. 上传小节封面（如有）
        cover_resource_id, _ = _upload_image_if_needed(
            client, section_cover_path, media_type="picweike"
        )

        # 3. 调用 update_scorm_session
        result = builder.update_scorm_session(
            group_id=group_id,
            session_id=section_id,
            session_title=section_title,
            resource_id=actual_resource_id,
            cover_resource_id=cover_resource_id,
            is_required=is_required,
            duration_minutes=duration_minutes,
        )

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="SCORM 小节更新成功。可继续修改其他小节，或在前端查看课程",
        )

    except RuntimeError as e:
        logger.error("更新 SCORM 小节失败: %s", e)
        return _err(
            error_code="UPDATE_SCORM_SECTION_FAILED",
            error_message=str(e),
            suggested_action="检查参数和 SCORM 文件后重试",
        )
    except Exception as e:
        logger.exception("更新 SCORM 小节异常")
        return _err(
            error_code="UPDATE_SCORM_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def tch_delete_section(
    group_id: Annotated[str, Field(description="课程 ID")],
    section_id: Annotated[
        str,
        Field(description="小节 ID（即 savesession 返回的 session_id），要删除的现有小节"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """删除课程中的小节.

    触发条件：需要移除课程中的某个小节时调用。
    前置依赖：需先调用 tch_login 完成登录，并已有 group_id 和 section_id。

    内部流程：
    1. 验证小节存在于课程中（调用 getgroupinfo 确认）
    2. 调用 e_deleteSession 删除小节

    返回：包含 deleted=True 和 session_id 的 JSON
    """
    client = _get_client(session_id)

    try:
        builder = CourseBuilder(client)
        result = builder.delete_session(
            group_id=group_id,
            session_id=section_id,
        )

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="小节已删除。如需添加新小节，调用 tch_create_scorm_section",
        )

    except RuntimeError as e:
        logger.error("删除小节失败: %s", e)
        return _err(
            error_code="DELETE_SECTION_FAILED",
            error_message=str(e),
            suggested_action="请确认 group_id 和 section_id 正确后重试",
        )
    except Exception as e:
        logger.exception("删除小节异常")
        return _err(
            error_code="DELETE_SECTION_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def tch_list_sections(
    group_id: Annotated[str, Field(description="课程 ID")],
    fuzzy_title: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的小节标题模糊匹配关键词。提供时会从课程全部小节中"
            "筛选最匹配的候选，并返回相似度分数。",
        ),
    ] = None,
    top_k: Annotated[
        int,
        Field(default=10, ge=1, le=100, description="模糊匹配时最多返回的候选数量"),
    ] = 10,
    similarity_threshold: Annotated[
        float,
        Field(
            default=0.3,
            ge=0.0,
            le=1.0,
            description="模糊匹配的最小相似度阈值（0.0 ~ 1.0）",
        ),
    ] = 0.3,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """列出课程中的所有小节.

    触发条件：需要查看课程的小节列表、查找小节 ID 用于后续编辑或删除时调用。
    前置依赖：需先调用 tch_login 完成登录。

    返回每个小节的结构化摘要：
    - session_id: 小节ID（用于 tch_update_scorm_section / tch_update_document_section / tch_toggle_section_visibility / tch_delete_section）
    - title: 小节标题
    - type: 小节类型（scorm / document）
    - is_required: 是否必修
    - resource_id: 绑定的资源ID
    - resource_type: 资源类型（video / document）
    - cover_resource_id: 封面资源ID
    - resource_status: 资源状态（含 is_recycle 字段，"1" 表示资源已被删除到回收站）
    - is_resource_deleted: 资源是否已被删除（true 表示在回收站中）
    - status: 小节状态

    返回：小节列表 JSON
    """
    client = _get_client(session_id)

    try:
        builder = CourseBuilder(client)
        sections = builder.list_sections(group_id)

        if fuzzy_title and fuzzy_title.strip():
            sections = fuzzy_filter_items(
                sections,
                fuzzy_title,
                key="title",
                top_k=top_k,
                similarity_threshold=similarity_threshold,
            )

        return _ok(
            data={
                "group_id": group_id,
                "count": len(sections),
                "sections": sections,
            },
            next_action="proceed",
            suggested_action="查看每个小节的 type 字段判断类型，然后选择对应工具：type='scorm' → 调用 tch_update_scorm_section；type='document' → 调用 tch_update_document_section；如需控制可见性（打开/关闭），调用 tch_toggle_section_visibility；如需查看完整详情，调用 tch_get_section；如需删除，调用 tch_delete_section",
        )
    except Exception as e:
        logger.exception("获取小节列表失败")
        return _err(
            error_code="LIST_SECTIONS_FAILED",
            error_message=str(e),
            suggested_action="请检查 group_id 是否正确，或稍后重试",
        )


@mcp.tool()
async def tch_get_section(
    section_id: Annotated[
        str,
        Field(description="小节 ID（即 savesession 返回的 session_id）"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取单个小节的完整详情.

    触发条件：需要查看小节当前状态、确认资源绑定关系、或准备编辑前调用。
    前置依赖：需先调用 tch_login 完成登录。

    返回的数据已过滤掉只读统计字段（如 weikeStat、liveStat 等）。

    返回：包含 sessionInfo（已过滤）和 sectionArr 的 JSON
    """
    client = _get_client(session_id)

    try:
        builder = CourseBuilder(client)
        detail = builder.get_section(section_id)

        return _ok(
            data=detail,
            next_action="proceed",
            suggested_action="如需修改此小节，根据类型调用 tch_update_scorm_section（SCORM）或 tch_update_document_section（文档）；如需控制可见性，调用 tch_toggle_section_visibility；如需删除，调用 tch_delete_section",
        )
    except Exception as e:
        logger.exception("获取小节详情失败")
        return _err(
            error_code="GET_SECTION_FAILED",
            error_message=str(e),
            suggested_action="请检查 section_id 是否正确，或稍后重试",
        )


# ---------------------------------------------------------------------------
# Tools: 课程信息获取与修改
# ---------------------------------------------------------------------------

@mcp.tool()
async def tch_get_categories(
    fuzzy_name: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的分类名称模糊匹配关键词。提供时会从全部分类中"
            "筛选最匹配的候选，并返回相似度分数。",
        ),
    ] = None,
    top_k: Annotated[
        int,
        Field(default=10, ge=1, le=100, description="模糊匹配时最多返回的候选数量"),
    ] = 10,
    similarity_threshold: Annotated[
        float,
        Field(
            default=0.3,
            ge=0.0,
            le=1.0,
            description="模糊匹配的最小相似度阈值（0.0 ~ 1.0）",
        ),
    ] = 0.3,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取当前账号的课程分类树.

    触发条件：需要在创建或修改课程前了解可用的分类选项时调用。
    前置依赖：需先调用 tch_login 完成登录。

    不同账号的分类树不同，此工具会动态从当前登录账号的页面数据中提取
    完整的分类树。返回的树形结构包含每个分类的 ID、名称和层级关系。

    返回：分类树 JSON，包含 tree（嵌套结构）和 flat（扁平列表）两种形式
    """
    client = _get_client(session_id)

    try:
        builder = CourseBuilder(client)
        tree = builder.get_category_tree()

        # 构建扁平列表（便于搜索和查看）
        flat_list: list[dict[str, Any]] = []

        def walk(node: dict[str, Any], path: list[str]) -> None:
            node_id = str(node.get("id", ""))
            node_name = str(node.get("name", ""))
            current_path = path + [node_name]
            flat_list.append({
                "id": node_id,
                "name": node_name,
                "parent_id": str(node.get("parent_id", "")),
                "path": " > ".join(current_path),
            })
            for sub in node.get("sub_category", []):
                walk(sub, current_path)

        for root in tree:
            walk(root, [])

        if fuzzy_name and fuzzy_name.strip():
            flat_list = fuzzy_filter_items_multi_key(
                flat_list,
                fuzzy_name,
                keys=["name", "path"],
                top_k=top_k,
                similarity_threshold=similarity_threshold,
            )

        return _ok(
            data={
                "tree": tree,
                "flat": flat_list,
                "total_count": len(flat_list),
            },
            next_action="proceed",
            suggested_action="查看可用分类后，调用 tch_create_course 或 tch_update_course 设置 category_names",
        )
    except Exception as e:
        logger.exception("获取分类树失败")
        return _err(
            error_code="GET_CATEGORIES_FAILED",
            error_message=str(e),
            suggested_action="请确保已登录，或稍后重试",
        )


@mcp.tool()
async def tch_get_course(
    group_id: Annotated[str, Field(description="课程 ID")],
    include_fulltext: Annotated[
        bool,
        Field(default=False, description="是否同时获取富文本 HTML 内容"),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取课程的完整可修改信息.

    触发条件：需要查看或了解某门课程的当前配置时调用。
    前置依赖：需先调用 tch_login 完成登录。

    返回的课程信息包含所有可通过 tch_update_course 修改的字段，
    过滤掉了系统自动填充的只读字段（如学员统计、分享链接等）。

    返回：包含课程信息的 JSON
    """
    client = _get_client(session_id)

    try:
        builder = CourseBuilder(client)
        info = builder.get_course(group_id, include_fulltext=include_fulltext)

        return _ok(
            data=info,
            next_action="proceed",
            suggested_action="如需修改课程，调用 tch_update_course 并传入需要变更的字段",
        )
    except Exception as e:
        logger.exception("获取课程信息失败")
        return _err(
            error_code="GET_COURSE_FAILED",
            error_message=str(e),
            suggested_action="请检查 group_id 是否正确，或稍后重试",
        )


@mcp.tool()
async def tch_get_course_detail(
    group_id: Annotated[str, Field(description="课程 ID")],
    include_fulltext: Annotated[
        bool,
        Field(default=False, description="是否同时获取富文本 HTML 内容"),
    ] = False,
    check_resource_status: Annotated[
        bool,
        Field(
            default=True,
            description="是否检测每个小节绑定资源的删除状态。开启后会检查 resource_info.is_recycle 字段，标记被删除到回收站的资源。",
        ),
    ] = True,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取课程的完整详情，包含小节列表和绑定资源删除状态检测.

    触发条件：需要全面了解某门课程的所有信息时调用，包括：
    - 课程基本信息（标题、描述、设置等）
    - 小节列表（每个小节的类型、规则、资源绑定）
    - 检测哪些小节绑定的资源已被删除（进入回收站）

    这是 tch_get_course + tch_list_sections 的超集，额外提供：
    - 每个小节的完整规则配置（vlt_min/vlt_max、完成条件等）
    - 资源删除状态检测（is_resource_deleted + resource_status）
    - 被删除资源的小节统计（deleted_resource_count）

    前置依赖：需先调用 tch_login 完成登录。

    返回的数据结构：
    {
      "course_info": { 课程基本信息 },
      "sections": [
        {
          "session_id": "...",
          "title": "小节标题",
          "type": "scorm|document",
          "is_required": true|false,
          "resource_id": "绑定的资源ID",
          "resource_type": "video|document",
          "resource_status": { "id": "...", "status": "in_use", "is_recycle": "0|1" },
          "is_resource_deleted": true|false,
          "rules": { "vlt_min": 60, "document_finished_condition": "2", ... }
        },
        ...
      ],
      "section_count": 8,
      "deleted_resource_count": 1
    }

    返回：包含课程详情和小节列表的 JSON
    """
    client = _get_client(session_id)

    try:
        builder = CourseBuilder(client)
        detail = builder.get_course_detail(
            group_id,
            include_fulltext=include_fulltext,
            check_resource_status=check_resource_status,
        )

        # 构建建议操作
        deleted_count = detail.get("deleted_resource_count", 0)
        if deleted_count > 0:
            deleted_sections = [
                s for s in detail.get("sections", [])
                if s.get("is_resource_deleted")
            ]
            deleted_names = ", ".join(
                f"'{s['title']}'({s['session_id']})" for s in deleted_sections
            )
            suggested = (
                f"检测到 {deleted_count} 个小节绑定的资源已被删除: {deleted_names}。"
                f"这些资源在回收站中，小节可能无法正常显示。"
                f"建议调用 tch_update_scorm_section 或 tch_update_document_section 重新绑定有效资源。"
            )
        else:
            suggested = (
                "所有小节资源状态正常。如需修改课程，调用 tch_update_course；"
                "如需修改小节，根据类型调用 tch_update_scorm_section 或 tch_update_document_section"
            )

        return _ok(
            data=detail,
            next_action="proceed",
            suggested_action=suggested,
        )
    except Exception as e:
        logger.exception("获取课程详情失败")
        return _err(
            error_code="GET_COURSE_DETAIL_FAILED",
            error_message=str(e),
            suggested_action="请检查 group_id 是否正确，或稍后重试",
        )


@mcp.tool()
async def tch_update_course(
    group_id: Annotated[str, Field(description="课程 ID，要修改的课程")],
    title: Annotated[
        str | None,
        Field(default=None, description="课程标题"),
    ] = None,
    desc: Annotated[
        str | None,
        Field(default=None, description="纯文本课程描述"),
    ] = None,
    remark: Annotated[
        str | None,
        Field(default=None, description="课程备注/副标题"),
    ] = None,
    lesson_type: Annotated[
        int | None,
        Field(
            default=None,
            description="课程形式: 0=线上课程, 1=面授培训, 2=混合式课程, 999=其他",
        ),
    ] = None,
    other_lesson_type: Annotated[
        str | None,
        Field(default=None, description="自定义课程形式（当 lesson_type=999 时生效）"),
    ] = None,
    category_ids: Annotated[
        list[str] | None,
        Field(default=None, description="课程分类 ID 列表，如 ['28484', '41230']。与 category_names 二选一"),
    ] = None,
    category_names: Annotated[
        list[str] | None,
        Field(
            default=None,
            description='课程分类名称列表，支持完整路径如 ["课程系列 > 新能力系列 > 客户思维"]。'
                        "与 category_ids 同时提供时，category_names 优先。"
                        "可先调用 tch_get_categories 查看可用分类。",
        ),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Field(default=None, description="课程标签列表，如 ['合规', '2025']"),
    ] = None,
    start_time: Annotated[
        str | None,
        Field(
            default=None,
            description="课程开始时间，ISO 8601 格式，如 '2026-06-01T09:00:00' 或 '2026-06-01'",
        ),
    ] = None,
    end_time: Annotated[
        str | None,
        Field(
            default=None,
            description="课程结束时间，ISO 8601 格式，如 '2026-06-06T18:00:00' 或 '2026-06-06'",
        ),
    ] = None,
    cover_image_path: Annotated[
        str | None,
        Field(default=None, description="课程封面图本地路径（jpg/png），上传后替换现有封面"),
    ] = None,
    bg_image_path: Annotated[
        str | None,
        Field(default=None, description="课程背景图本地路径（jpg/png），上传后替换现有背景"),
    ] = None,
    desc_richtext: Annotated[
        str | None,
        Field(default=None, description="富文本课程介绍（HTML），会替换现有的纯文本描述"),
    ] = None,
    desc_richtext_images: Annotated[
        list[str] | None,
        Field(
            default=None,
            description="富文本中引用的本地图片路径列表，会逐个上传并替换 HTML 中的 src",
        ),
    ] = None,
    province: Annotated[
        str | None,
        Field(default=None, description="省份"),
    ] = None,
    city: Annotated[
        str | None,
        Field(default=None, description="城市"),
    ] = None,
    town: Annotated[
        str | None,
        Field(default=None, description="区县"),
    ] = None,
    address: Annotated[
        str | None,
        Field(default=None, description="详细地址"),
    ] = None,
    contact: Annotated[
        str | None,
        Field(default=None, description="联系人"),
    ] = None,
    contact_phone: Annotated[
        str | None,
        Field(default=None, description="联系电话"),
    ] = None,
    customer_name: Annotated[
        str | None,
        Field(default=None, description="客户名称"),
    ] = None,
    course_person: Annotated[
        str | None,
        Field(default=None, description="课程负责人"),
    ] = None,
    max_online_user: Annotated[
        str | None,
        Field(default=None, description="最大在线人数限制"),
    ] = None,
    max_user_count: Annotated[
        str | None,
        Field(default=None, description="最大报名人数限制"),
    ] = None,
    is_important: Annotated[
        bool | None,
        Field(default=None, description="是否标记为重要课程"),
    ] = None,
    enroll_status: Annotated[
        int | None,
        Field(default=None, description="报名设置: 0=不需要报名, 1=需要报名"),
    ] = None,
    setup: Annotated[
        dict[str, Any] | None,
        Field(default=None, description="高级课程设置字典（如需修改特定 setup 字段时使用）"),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """修改已有课程的信息.

    触发条件：需要修改课程的标题、描述、形式、分类、时间、封面等任何信息时调用。
    前置依赖：需先调用 tch_login 完成登录，且课程已存在。

    重要特性：
    - 只更新传入的参数，未传入的字段保持原值不变
    - 所有参数均为可选，但至少需要传入一个要修改的字段
    - 修改封面/背景图时会自动上传到 COS
    - 修改富文本时会自动处理图片上传
    - 报名设置（enroll_status）会走独立的 /api/enroll/saveenroll 接口，
      不再通过 e_saveGroup 提交，因此会真正生效

    标准使用流程：
    1. 调用 tch_get_course(group_id) 查看当前配置
    2. 调用 tch_update_course(group_id, ...) 传入需要变更的字段
    3. 再次调用 tch_get_course 验证修改结果

    返回：包含变更字段列表的 JSON
    """
    client = _get_client(session_id)

    # 检查至少有一个变更参数
    all_params = {
        "title": title,
        "desc": desc,
        "remark": remark,
        "lesson_type": lesson_type,
        "other_lesson_type": other_lesson_type,
        "category_ids": category_ids,
        "category_names": category_names,
        "tags": tags,
        "start_time": start_time,
        "end_time": end_time,
        "cover_image_path": cover_image_path,
        "bg_image_path": bg_image_path,
        "desc_richtext": desc_richtext,
        "desc_richtext_images": desc_richtext_images,
        "province": province,
        "city": city,
        "town": town,
        "address": address,
        "contact": contact,
        "contact_phone": contact_phone,
        "customer_name": customer_name,
        "course_person": course_person,
        "max_online_user": max_online_user,
        "max_user_count": max_user_count,
        "is_important": is_important,
        "enroll_status": enroll_status,
        "setup": setup,
    }
    provided = {k: v for k, v in all_params.items() if v is not None}

    if not provided:
        return _err(
            error_code="NO_CHANGES",
            error_message="未提供任何要修改的字段",
            suggested_action="至少传入一个要修改的参数，如 title='新标题' 或 lesson_type=1",
        )

    try:
        builder = CourseBuilder(client)
        enroll_result: dict[str, Any] | None = None

        # 报名设置有独立的 API，不通过 e_saveGroup 持久化
        if enroll_status is not None:
            enroll_result = builder.set_course_enrollment(
                group_id=group_id,
                enabled=bool(enroll_status),
            )

        # 其他字段通过 update_course 修改（不包含 enroll_status）
        other_params = {
            "group_id": group_id,
            "title": title,
            "desc": desc,
            "remark": remark,
            "lesson_type": lesson_type,
            "other_lesson_type": other_lesson_type,
            "category_ids": category_ids,
            "category_names": category_names,
            "tags": tags,
            "start_time": start_time,
            "end_time": end_time,
            "cover_image_path": cover_image_path,
            "bg_image_path": bg_image_path,
            "desc_richtext": desc_richtext,
            "desc_richtext_images": desc_richtext_images,
            "province": province,
            "city": city,
            "town": town,
            "address": address,
            "contact": contact,
            "contact_phone": contact_phone,
            "customer_name": customer_name,
            "course_person": course_person,
            "max_online_user": max_online_user,
            "max_user_count": max_user_count,
            "is_important": is_important,
            "setup": setup,
        }
        other_provided = {k: v for k, v in other_params.items() if v is not None and k != "group_id"}

        if other_provided:
            result = builder.update_course(**other_params)
        else:
            result = {"group_id": group_id, "changes": []}

        if enroll_result:
            result["enroll"] = enroll_result
            result["changes"] = result.get("changes", []) + ["enroll_status"]

        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="可调用 tch_get_course 验证修改结果",
        )
    except Exception as e:
        logger.exception("修改课程失败")
        return _err(
            error_code="UPDATE_COURSE_FAILED",
            error_message=str(e),
            suggested_action="请检查参数和网络后重试",
        )


@mcp.tool()
async def tch_get_course_enrollment(
    group_id: Annotated[str, Field(description="课程 ID，要查询报名的课程")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取课程当前报名配置.

    触发条件：需要查看课程报名开关、标题、名额、时间、价格、联系信息、
    自定义问题等配置时调用。

    返回字段包含：enroll_id、title、status、auto_check、multimedia_id、
    setup（限额/时间/开关等）、setupInfo（share/payment）、sectionArr、
    contactInfo 等。
    """
    client = _get_client(session_id)
    try:
        builder = CourseBuilder(client)
        result = builder.get_course_enrollment(group_id)
        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="可调用 tch_set_course_enrollment 修改报名配置",
        )
    except Exception as e:
        logger.exception("获取课程报名配置失败")
        return _err(
            error_code="GET_ENROLLMENT_FAILED",
            error_message=str(e),
            suggested_action="请检查 group_id 和登录状态后重试",
        )


@mcp.tool()
async def tch_set_course_enrollment(
    group_id: Annotated[str, Field(description="课程 ID，要设置报名的课程")],
    enabled: Annotated[
        bool,
        Field(default=True, description="是否开启报名: True=开启, False=关闭"),
    ] = True,
    auto_check: Annotated[
        bool,
        Field(default=True, description="是否自动审核报名"),
    ] = True,
    title: Annotated[
        str | None,
        Field(default=None, description="报名页标题，默认使用课程标题"),
    ] = None,
    desc: Annotated[
        str,
        Field(default="", description="报名说明/介绍"),
    ] = "",
    allow_cancel: Annotated[
        bool,
        Field(default=False, description="是否允许学员取消报名"),
    ] = False,
    user_quota: Annotated[
        int,
        Field(default=-1, description="报名人数上限，-1 表示不限制"),
    ] = -1,
    begin_time: Annotated[
        int | str,
        Field(default=0, description="报名开始时间（Unix 时间戳或 0 表示不限制）"),
    ] = 0,
    end_time: Annotated[
        int | str,
        Field(default=0, description="报名结束时间（Unix 时间戳或 0 表示不限制）"),
    ] = 0,
    price_amount: Annotated[
        int,
        Field(default=0, description="报名价格（分），0 表示免费"),
    ] = 0,
    selected_contact_fields: Annotated[
        str | None,
        Field(
            default=None,
            description='要勾选的报名联系信息字段 key 列表，JSON 格式: ["username", "mobile"]。'
                        "不传则不勾选任何字段。",
        ),
    ] = None,
    contact_info_json: Annotated[
        str | None,
        Field(
            default=None,
            description='自定义报名联系信息字段 JSON，格式: [{"key": "...", "questionTitle": "...", '
                        '"defaultPlaceHolder": "...", "domType": "text", "isSelected": true, '
                        '"isRequired": true}]。isSelected 控制是否勾选，isRequired 控制是否必填。'
                        "不传则使用系统默认字段。",
        ),
    ] = None,
    section_questions_json: Annotated[
        str | None,
        Field(
            default=None,
            description='自定义报名问题 JSON，格式: [{"title": "问题", "type": "radio", '
                        '"required": true, "options": [{"text": "选项1"}, {"text": "选项2"}]}]。'
                        "支持 textarea（开放式问题）/radio/checkbox/paragraph/number；"
                        "简写 type=text 会被映射为 textarea。必填问题的 required 请传 true。",
        ),
    ] = None,
    approval_setting_json: Annotated[
        str | None,
        Field(
            default=None,
            description='审核人设置 JSON，格式: {"course_manager": true, "department_manager": false, '
                        '"designee": false}。自动审核时通常无需设置。',
        ),
    ] = None,
    enroll_id: Annotated[
        str,
        Field(default="", description="现有报名 ID，修改报名时传入"),
    ] = "",
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """设置课程报名开关及报名信息.

    触发条件：需要开启或关闭课程报名时调用。
    前置依赖：需先调用 tch_login 完成登录，且课程已存在。

    注意：UMU 的报名开关不通过 e_saveGroup 持久化，必须使用独立的
    /api/enroll/saveenroll 接口，本工具已封装该调用。
    """
    client = _get_client(session_id)

    def _parse_json(name: str, value: str | None) -> Any:
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError as e:
            raise ValueError(f"{name} 不是有效 JSON: {e}")

    try:
        contact_info = _parse_json("contact_info_json", contact_info_json)
        section_questions = _parse_json("section_questions_json", section_questions_json)
        approval_setting = _parse_json("approval_setting_json", approval_setting_json)
        selected_fields = _parse_json("selected_contact_fields", selected_contact_fields)
    except ValueError as e:
        return _err(
            error_code="INVALID_JSON",
            error_message=str(e),
            suggested_action="请检查 JSON 格式",
        )

    try:
        builder = CourseBuilder(client)
        result = builder.set_course_enrollment(
            group_id=group_id,
            enabled=enabled,
            auto_check=auto_check,
            title=title,
            desc=desc,
            allow_cancel=allow_cancel,
            user_quota=user_quota,
            begin_time=begin_time,
            end_time=end_time,
            price_amount=price_amount,
            selected_contact_fields=selected_fields,
            contact_info=contact_info,
            section_questions=section_questions,
            approval_setting=approval_setting,
            enroll_id=enroll_id,
        )
        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="可调用 tch_get_course 查看 enroll_status 字段验证结果",
        )
    except Exception as e:
        logger.exception("设置课程报名失败")
        return _err(
            error_code="SET_ENROLLMENT_FAILED",
            error_message=str(e),
            suggested_action="请检查 group_id 和网络后重试",
        )


# ---------------------------------------------------------------------------
# Tools: 原子化课程修改（tch_update_course 的细粒度拆分）
# ---------------------------------------------------------------------------

@mcp.tool()
async def tch_update_course_basic(
    group_id: Annotated[str, Field(description="课程 ID，要修改的课程")],
    title: Annotated[
        str | None,
        Field(default=None, description="课程标题"),
    ] = None,
    desc: Annotated[
        str | None,
        Field(default=None, description="纯文本课程描述"),
    ] = None,
    remark: Annotated[
        str | None,
        Field(default=None, description="课程备注/副标题"),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Field(default=None, description="课程标签列表，如 ['合规', '2025']"),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """修改课程的基础信息（标题、描述、备注、标签）.

    只更新传入的字段，未传入的字段保持原值不变。
    用于快速调整课程的文本类基础信息。
    """
    client = _get_client(session_id)

    if all(v is None for v in [title, desc, remark, tags]):
        return _err(
            error_code="NO_CHANGES",
            error_message="未提供任何要修改的字段",
            suggested_action="至少传入 title、desc、remark 或 tags 之一",
        )

    try:
        builder = CourseBuilder(client)
        result = builder.update_course(
            group_id=group_id,
            title=title,
            desc=desc,
            remark=remark,
            tags=tags,
        )
        return _ok(data=result, next_action="proceed")
    except Exception as e:
        logger.exception("修改课程基础信息失败")
        return _err(
            error_code="UPDATE_BASIC_FAILED",
            error_message=str(e),
            suggested_action="请检查 group_id 是否正确，以及字段格式是否有效",
        )


@mcp.tool()
async def tch_update_course_type(
    group_id: Annotated[str, Field(description="课程 ID，要修改的课程")],
    lesson_type: Annotated[
        int | None,
        Field(
            default=None,
            description="课程形式: 0=线上课程, 1=面授培训, 2=混合式课程, 999=其他",
        ),
    ] = None,
    other_lesson_type: Annotated[
        str | None,
        Field(default=None, description="自定义课程形式（当 lesson_type=999 时生效）"),
    ] = None,
    content_type: Annotated[
        str | None,
        Field(default=None, description="内容类型"),
    ] = None,
    other_content_type: Annotated[
        str | None,
        Field(default=None, description="自定义内容类型"),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """修改课程的形式和类型.

    用于切换课程是在线课程、面授培训还是混合式课程。
    """
    client = _get_client(session_id)

    if all(v is None for v in [lesson_type, other_lesson_type, content_type, other_content_type]):
        return _err(
            error_code="NO_CHANGES",
            error_message="未提供任何要修改的字段",
            suggested_action="至少传入 lesson_type 或其他类型字段",
        )

    try:
        builder = CourseBuilder(client)
        result = builder.update_course(
            group_id=group_id,
            lesson_type=lesson_type,
            other_lesson_type=other_lesson_type,
            content_type=content_type,
            other_content_type=other_content_type,
        )
        return _ok(data=result, next_action="proceed")
    except Exception as e:
        logger.exception("修改课程形式失败")
        return _err(
            error_code="UPDATE_TYPE_FAILED",
            error_message=str(e),
            suggested_action="请检查 group_id 是否正确，lesson_type 取值范围: 0/1/2/999",
        )


@mcp.tool()
async def tch_update_course_category(
    group_id: Annotated[str, Field(description="课程 ID，要修改的课程")],
    category_ids: Annotated[
        list[str] | None,
        Field(default=None, description="课程分类 ID 列表，如 ['28484', '41230']。与 category_names 二选一"),
    ] = None,
    category_names: Annotated[
        list[str] | None,
        Field(
            default=None,
            description='课程分类名称列表，支持完整路径如 ["课程系列 > 新能力系列 > 客户思维"]。'
                        "与 category_ids 同时提供时，category_names 优先。"
                        "可先调用 tch_get_categories 查看可用分类。",
        ),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """修改课程的分类.

    分类名称支持完整路径匹配，可消除同名分类的歧义。
    如 "课程系列 > 新能力系列 > 客户思维" 可精确定位目标分类。
    """
    client = _get_client(session_id)

    if category_ids is None and category_names is None:
        return _err(
            error_code="NO_CHANGES",
            error_message="未提供分类信息",
            suggested_action="传入 category_ids 或 category_names",
        )

    try:
        builder = CourseBuilder(client)
        result = builder.update_course(
            group_id=group_id,
            category_ids=category_ids,
            category_names=category_names,
        )
        return _ok(data=result, next_action="proceed")
    except Exception as e:
        logger.exception("修改课程分类失败")
        return _err(
            error_code="UPDATE_CATEGORY_FAILED",
            error_message=str(e),
            suggested_action="请检查 group_id 是否正确，分类名称是否有效。可先调用 tch_get_categories 查看可用分类",
        )


@mcp.tool()
async def tch_update_course_schedule(
    group_id: Annotated[str, Field(description="课程 ID，要修改的课程")],
    course_start_date: Annotated[
        str | None,
        Field(
            default=None,
            description="课程有效期开始日期，格式 YYYY-MM-DD（如 '2026-06-01'）",
        ),
    ] = None,
    course_end_date: Annotated[
        str | None,
        Field(
            default=None,
            description="课程有效期结束日期，格式 YYYY-MM-DD（如 '2026-06-30'）",
        ),
    ] = None,
    session_date: Annotated[
        str | None,
        Field(
            default=None,
            description="上课日期，格式 YYYY-MM-DD（如 '2026-06-01'）",
        ),
    ] = None,
    session_start_time: Annotated[
        str | None,
        Field(
            default=None,
            description="上课开始时间，格式 HH:MM（如 '09:00'）",
        ),
    ] = None,
    session_end_time: Annotated[
        str | None,
        Field(
            default=None,
            description="上课结束时间，格式 HH:MM（如 '10:00'）",
        ),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """修改课程的时间安排（有效期和上课时段）.

    参数语义清晰分离：
    - course_start_date / course_end_date：课程的有效期（日期级别）
    - session_date / session_start_time / session_end_time：具体上课时间（精确到分钟）

    如果只修改有效期而不改上课时间，只需传入 course_start_date / course_end_date。
    如果同时修改上课时段，需要传入 session_date + session_start_time + session_end_time。
    """
    client = _get_client(session_id)

    if all(v is None for v in [course_start_date, course_end_date, session_date, session_start_time, session_end_time]):
        return _err(
            error_code="NO_CHANGES",
            error_message="未提供任何时间参数",
            suggested_action="至少传入一个时间参数，如 course_start_date='2026-06-01'",
        )

    try:
        builder = CourseBuilder(client)
        result = builder.update_course(
            group_id=group_id,
            course_start_date=course_start_date,
            course_end_date=course_end_date,
            session_date=session_date,
            session_start_time=session_start_time,
            session_end_time=session_end_time,
        )
        return _ok(data=result, next_action="proceed")
    except Exception as e:
        logger.exception("修改课程时间安排失败")
        return _err(
            error_code="UPDATE_SCHEDULE_FAILED",
            error_message=str(e),
            suggested_action="请检查 group_id 是否正确，日期格式应为 YYYY-MM-DD，时间格式应为 HH:MM",
        )


@mcp.tool()
async def tch_update_course_images(
    group_id: Annotated[str, Field(description="课程 ID，要修改的课程")],
    cover_image_path: Annotated[
        str | None,
        Field(default=None, description="课程封面图本地路径（jpg/png），上传后替换现有封面"),
    ] = None,
    bg_image_path: Annotated[
        str | None,
        Field(default=None, description="课程背景图本地路径（jpg/png），上传后替换现有背景"),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """修改课程的封面图和/或背景图.

    图片会自动上传到 COS 并获取 URL，然后更新到课程中。
    """
    client = _get_client(session_id)

    if cover_image_path is None and bg_image_path is None:
        return _err(
            error_code="NO_CHANGES",
            error_message="未提供任何图片路径",
            suggested_action="传入 cover_image_path 或 bg_image_path",
        )

    try:
        builder = CourseBuilder(client)
        result = builder.update_course(
            group_id=group_id,
            cover_image_path=cover_image_path,
            bg_image_path=bg_image_path,
        )
        return _ok(data=result, next_action="proceed")
    except Exception as e:
        logger.exception("修改课程图片失败")
        return _err(
            error_code="UPDATE_IMAGES_FAILED",
            error_message=str(e),
            suggested_action="请检查图片文件路径是否正确，格式应为 jpg/png",
        )


@mcp.tool()
async def tch_update_course_richtext(
    group_id: Annotated[str, Field(description="课程 ID，要修改的课程")],
    desc_richtext: Annotated[
        str,
        Field(description="富文本课程介绍（HTML）"),
    ],
    desc_richtext_images: Annotated[
        list[str] | None,
        Field(
            default=None,
            description="富文本中引用的本地图片路径列表，会逐个上传并替换 HTML 中的 src",
        ),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """修改课程的富文本介绍.

    如果课程当前没有富文本，会创建新的富文本内容。
    如果提供了 desc_richtext_images，会先上传这些图片到 COS，
    然后将 HTML 中的本地路径替换为 COS URL。
    """
    client = _get_client(session_id)

    try:
        builder = CourseBuilder(client)
        result = builder.update_course(
            group_id=group_id,
            desc_richtext=desc_richtext,
            desc_richtext_images=desc_richtext_images,
        )
        return _ok(data=result, next_action="proceed")
    except Exception as e:
        logger.exception("修改课程富文本失败")
        return _err(
            error_code="UPDATE_RICHTEXT_FAILED",
            error_message=str(e),
            suggested_action="请检查 group_id 是否正确，HTML 内容是否有效",
        )


@mcp.tool()
async def tch_submit_course_for_audit(
    group_id: Annotated[str, Field(description="课程 ID，要提交审核的课程")],
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """将课程提交至企业知识库进行审核.

    触发条件：讲师希望课程被管理员审核、推荐并支持搜索时调用。
    前置依赖：需先调用 tch_login 完成登录，且课程已存在。

    提交后课程状态会变为待审核（audit_status=0, release_status=2）。
    课程仍可继续编辑，更新内容会同步到已提交的版本中。

    返回：包含 release_status、audit_status 等字段的 JSON
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 tch_login 完成登录后再重试",
        )

    try:
        builder = CourseBuilder(client)
        result = builder.submit_course_for_audit(group_id=group_id)

        return _ok(
            data={
                "group_id": group_id,
                "release_status": result.get("data", {}).get("release_status"),
                "audit_status": result.get("data", {}).get("audit_status"),
            },
            next_action="proceed",
            suggested_action="提交成功，等待管理员审核。可调用 tch_get_course 查看最新状态",
        )
    except Exception as e:
        logger.exception("提交课程审核失败")
        return _err(
            error_code="SUBMIT_COURSE_FOR_AUDIT_FAILED",
            error_message=str(e),
            suggested_action="请检查 group_id 是否正确，或稍后重试",
        )


# ---------------------------------------------------------------------------
# Tools: 课程列表查询（我的课程）
# ---------------------------------------------------------------------------

@mcp.tool()
async def tch_list_created_courses(
    page: Annotated[int, Field(default=1, ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Field(default=10, ge=1, le=100, description="每页数量，默认 10，最大 100")] = 10,
    order: Annotated[
        str,
        Field(default="update_time", description="排序方式：update_time=按更新时间, create_time=按创建时间"),
    ] = "update_time",
    fetch_all: Annotated[
        bool,
        Field(default=False, description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。"),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """获取当前讲师已创建的课程列表.

    触发条件：当需要查看讲师自己创建的所有课程时调用。
    前置依赖：需先调用 tch_login 完成登录。
    副作用：无（只读查询）。

    返回的课程列表包含 group_id、标题、封面图、访问码等信息。
    支持分页和排序，默认按更新时间倒序排列。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 tch_login 完成登录后再重试",
        )

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        resp = client.get(
            client.desktop_url("/api/group/getgrouplist"),
            params={
                "t": str(int(time.time() * 1000)),
                "from_type": "web",
                "order": order,
                "page": str(p),
                "size": str(sz),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取已创建课程列表失败"))

        data = resp.get("data", {})
        page_info = data.get("page_info", {})
        course_list = data.get("list", [])

        formatted_list = []
        for item in course_list:
            info = item.get("groupInfo", {})
            formatted_list.append({
                "group_id": info.get("id", ""),
                "title": info.get("title", ""),
                "teacher_name": info.get("teacher_name", ""),
                "teacher_id": info.get("teacher_id", ""),
                "access_code": info.get("access_code", ""),
                "cover_url": info.get("head_img", ""),
                "bg_url": info.get("bg_img", ""),
                "share_url": info.get("sharePcUrl", ""),
                "lesson_type": info.get("lesson_type", ""),
                "release_status": info.get("release_status", ""),
                "create_time": info.get("creat_time", ""),
                "update_time": info.get("update_time", ""),
                "stime": info.get("stime", ""),
                "etime": info.get("etime", ""),
            })

        total_all = int(page_info.get("list_total_num", 0) or 0)
        return formatted_list, total_all

    try:
        if fetch_all:
            batch_size = 50
            all_items: list[dict[str, Any]] = []
            total_all = 0
            current_page = 1

            while True:
                page_items, total_all = _fetch_page(current_page, batch_size)
                all_items.extend(page_items)

                report_pagination_progress(
                    "tch_list_created_courses",
                    current_page,
                    len(all_items),
                    total_all,
                    batch_size,
                )

                if not page_items or len(all_items) >= total_all:
                    report_pagination_progress(
                        "tch_list_created_courses",
                        current_page,
                        len(all_items),
                        total_all,
                        batch_size,
                        is_complete=True,
                    )
                    break

                if current_page >= 50:
                    report_pagination_progress(
                        "tch_list_created_courses",
                        current_page,
                        len(all_items),
                        total_all,
                        batch_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

                current_page += 1

            return _ok(
                data={
                    "courses": all_items,
                    "pagination": {
                        "total_all": total_all,
                        "current_page": current_page,
                        "page_size": batch_size,
                    },
                },
                next_action="proceed",
                suggested_action="可调用 tch_get_course(group_id) 获取课程详情，或翻页查看更多课程",
            )

        # Single-page mode (original behavior)
        formatted_list, _ = _fetch_page(page, page_size)

        return _ok(
            data={
                "courses": formatted_list,
                "pagination": {
                    "total": 0,
                    "total_pages": 0,
                    "current_page": page,
                    "page_size": page_size,
                },
            },
            next_action="proceed",
            suggested_action="可调用 tch_get_course(group_id) 获取课程详情，或翻页查看更多课程",
        )

    except Exception as e:
        logger.exception("获取已创建课程列表失败")
        return _err(
            error_code="LIST_CREATED_COURSES_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接后重试",
        )


@mcp.tool()
async def tch_list_cooperated_courses(
    page: Annotated[int, Field(default=1, ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Field(default=10, ge=1, le=100, description="每页数量，默认 10，最大 100")] = 10,
    order: Annotated[
        str,
        Field(default="update_time", description="排序方式：update_time=按更新时间, create_time=按创建时间"),
    ] = "update_time",
    fetch_all: Annotated[
        bool,
        Field(default=False, description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。"),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """获取别人协同给当前讲师的课程列表.

    触发条件：当需要查看别人协同给我的课程时调用。
    前置依赖：需先调用 tch_login 完成登录。
    副作用：无（只读查询）。

    协同课程是指其他讲师创建并授权我参与管理的课程。
    支持分页和排序，默认按更新时间倒序排列。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 tch_login 完成登录后再重试",
        )

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        resp = client.get(
            client.desktop_url("/api/group/getcooperategrouplist"),
            params={
                "t": str(int(time.time() * 1000)),
                "from_type": "web",
                "order": order,
                "page": str(p),
                "size": str(sz),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取协同课程列表失败"))

        data = resp.get("data", {})
        page_info = data.get("page_info", {})
        course_list = data.get("list", [])

        formatted_list = []
        for item in course_list:
            info = item.get("groupInfo", {})
            formatted_list.append({
                "group_id": info.get("id", ""),
                "title": info.get("title", ""),
                "teacher_name": info.get("teacher_name", ""),
                "teacher_id": info.get("teacher_id", ""),
                "access_code": info.get("access_code", ""),
                "cover_url": info.get("head_img", ""),
                "bg_url": info.get("bg_img", ""),
                "share_url": info.get("sharePcUrl", ""),
                "lesson_type": info.get("lesson_type", ""),
                "release_status": info.get("release_status", ""),
                "create_time": info.get("creat_time", ""),
                "update_time": info.get("update_time", ""),
                "stime": info.get("stime", ""),
                "etime": info.get("etime", ""),
            })

        total_all = int(page_info.get("list_total_num", 0) or 0)
        return formatted_list, total_all

    try:
        if fetch_all:
            batch_size = 50
            all_items: list[dict[str, Any]] = []
            total_all = 0
            current_page = 1

            while True:
                page_items, total_all = _fetch_page(current_page, batch_size)
                all_items.extend(page_items)

                report_pagination_progress(
                    "tch_list_cooperated_courses",
                    current_page,
                    len(all_items),
                    total_all,
                    batch_size,
                )

                if not page_items or len(all_items) >= total_all:
                    report_pagination_progress(
                        "tch_list_cooperated_courses",
                        current_page,
                        len(all_items),
                        total_all,
                        batch_size,
                        is_complete=True,
                    )
                    break

                if current_page >= 50:
                    report_pagination_progress(
                        "tch_list_cooperated_courses",
                        current_page,
                        len(all_items),
                        total_all,
                        batch_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

                current_page += 1

            return _ok(
                data={
                    "courses": all_items,
                    "pagination": {
                        "total_all": total_all,
                        "current_page": current_page,
                        "page_size": batch_size,
                    },
                },
                next_action="proceed",
                suggested_action="可调用 tch_get_course(group_id) 获取课程详情，或翻页查看更多课程",
            )

        # Single-page mode (original behavior)
        formatted_list, _ = _fetch_page(page, page_size)

        return _ok(
            data={
                "courses": formatted_list,
                "pagination": {
                    "total": 0,
                    "total_pages": 0,
                    "current_page": page,
                    "page_size": page_size,
                },
            },
            next_action="proceed",
            suggested_action="可调用 tch_get_course(group_id) 获取课程详情，或翻页查看更多课程",
        )

    except Exception as e:
        logger.exception("获取协同课程列表失败")
        return _err(
            error_code="LIST_COOPERATED_COURSES_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接后重试",
        )


@mcp.tool()
async def tch_list_participated_courses(
    page: Annotated[int, Field(default=1, ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Field(default=20, ge=1, le=100, description="每页数量，默认 20，最大 100")] = 20,
    learn_status: Annotated[
        int,
        Field(default=0, ge=0, le=3, description="学习状态筛选：0=所有, 1=已学习, 2=学习中, 3=待学习"),
    ] = 0,
    fetch_all: Annotated[
        bool,
        Field(default=False, description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。"),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """获取当前用户已参与（作为学员学习）的课程列表.

    触发条件：当需要查看我参与学习的所有课程时调用。
    前置依赖：需先调用 tch_login 完成登录。
    副作用：无（只读查询）。

    注意：讲师账号也可以作为学员参与课程学习，所以此工具对讲师同样有用。
    支持按学习状态筛选：0=所有, 1=已学习, 2=学习中, 3=待学习。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 tch_login 完成登录后再重试",
        )

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        resp = client.get(
            client.desktop_url("/api/group/getmyparticipatedgrouplist"),
            params={
                "t": str(int(time.time() * 1000)),
                "learn_status": str(learn_status),
                "page": str(p),
                "size": str(sz),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取已参与课程列表失败"))

        data = resp.get("data", {})
        page_info = data.get("page_info", {})
        course_list = data.get("list", [])

        status_map = {0: "all", 1: "pending", 2: "learning", 3: "completed"}

        formatted_list = []
        for item in course_list:
            formatted_list.append({
                "group_id": item.get("group_id", ""),
                "title": item.get("group_title", ""),
                "learn_status": item.get("learn_status", 0),
                "learn_status_label": status_map.get(item.get("learn_status", 0), "unknown"),
                "finish_ratio": item.get("finish_ratio", 0),
                "cover_url": item.get("show_pic", ""),
                "access_code": item.get("access_code", ""),
                "group_url": item.get("group_url", ""),
                "share_pc_url": item.get("share_pc_url", ""),
                "session_num": item.get("session_num", 0),
                "participant_time": item.get("participant_time", ""),
            })

        total_all = int(page_info.get("list_total_num", 0) or 0)
        return formatted_list, total_all

    try:
        if fetch_all:
            batch_size = 50
            all_items: list[dict[str, Any]] = []
            total_all = 0
            current_page = 1

            while True:
                page_items, total_all = _fetch_page(current_page, batch_size)
                all_items.extend(page_items)

                report_pagination_progress(
                    "tch_list_participated_courses",
                    current_page,
                    len(all_items),
                    total_all,
                    batch_size,
                )

                if not page_items or len(all_items) >= total_all:
                    report_pagination_progress(
                        "tch_list_participated_courses",
                        current_page,
                        len(all_items),
                        total_all,
                        batch_size,
                        is_complete=True,
                    )
                    break

                if current_page >= 50:
                    report_pagination_progress(
                        "tch_list_participated_courses",
                        current_page,
                        len(all_items),
                        total_all,
                        batch_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

                current_page += 1

            status_map = {0: "all", 1: "pending", 2: "learning", 3: "completed"}
            return _ok(
                data={
                    "courses": all_items,
                    "filter": {
                        "learn_status": learn_status,
                        "learn_status_label": status_map.get(learn_status, "unknown"),
                    },
                    "pagination": {
                        "total_all": total_all,
                        "current_page": current_page,
                        "page_size": batch_size,
                    },
                },
                next_action="proceed",
                suggested_action="可调用 tch_get_course(group_id) 获取课程详情，或切换 learn_status 筛选不同状态的课程",
            )

        # Single-page mode (original behavior)
        formatted_list, _ = _fetch_page(page, page_size)

        status_map = {0: "all", 1: "pending", 2: "learning", 3: "completed"}
        return _ok(
            data={
                "courses": formatted_list,
                "filter": {
                    "learn_status": learn_status,
                    "learn_status_label": status_map.get(learn_status, "unknown"),
                },
                "pagination": {
                    "total": 0,
                    "total_pages": 0,
                    "current_page": page,
                    "page_size": page_size,
                },
            },
            next_action="proceed",
            suggested_action="可调用 tch_get_course(group_id) 获取课程详情，或切换 learn_status 筛选不同状态的课程",
        )

    except Exception as e:
        logger.exception("获取已参与课程列表失败")
        return _err(
            error_code="LIST_PARTICIPATED_COURSES_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接后重试",
        )


# ---------------------------------------------------------------------------
# Tools: 学习项目列表查询
# ---------------------------------------------------------------------------

def _program_list_url_and_params(
    scope: str,
    keywords: str,
    page: int,
    page_size: int,
) -> tuple[str, dict[str, str]]:
    """根据 scope 返回学习项目列表的端点和参数."""
    base_params: dict[str, str] = {
        "t": str(int(time.time() * 1000)),
        "page": str(page),
        "size": str(page_size),
    }
    if scope == "owned":
        url = "/api/program/getlist"
        base_params["owner"] = "1"
        base_params["type"] = "1"
    elif scope == "cooperated":
        url = "/api/program/getcooperateprogramlist"
    elif scope == "enrolled":
        url = "/api/program/getmyparticipatedprogramlist"
    else:
        raise ValueError(f"不支持的 scope: {scope}")

    if keywords:
        base_params["keywords"] = keywords

    return url, base_params


def _format_program_list_item(item: dict[str, Any]) -> dict[str, Any]:
    """统一格式化 /api/program/getlist 返回的项目字段."""
    creator = item.get("creator", {}) or {}
    return {
        "program_id": str(item.get("program_id", "")),
        "program_title": item.get("program_title", ""),
        "desc": item.get("desc", ""),
        "access_code": item.get("access_code", ""),
        "share_url": item.get("share_url", ""),
        "share_pc_url": item.get("share_pc_url", ""),
        "head_img": item.get("head_img", ""),
        "bg_img": item.get("setup", {}).get("bg_img", ""),
        "create_time": item.get("create_time", ""),
        "update_time": item.get("update_time", ""),
        "creator_umu_id": str(creator.get("umu_id", "")),
        "creator_name": creator.get("user_name", item.get("creater_name", "")),
        "group_num": item.get("group_num", 0),
        "module_num": item.get("module_num", 0),
        "is_creator": item.get("is_creator", 0),
    }


@mcp.tool()
async def tch_list_learning_programs(
    scope: Annotated[
        str,
        Field(description="列表视角：owned=我拥有的, cooperated=协同给我的, enrolled=我报名的"),
    ],
    keywords: Annotated[
        str | None,
        Field(default=None, description="按标题/访问码模糊搜索"),
    ] = None,
    page: Annotated[int, Field(default=1, ge=1, description="页码")] = 1,
    page_size: Annotated[int, Field(default=20, ge=1, le=100, description="每页数量")] = 20,
    fetch_all: Annotated[
        bool,
        Field(default=False, description="是否自动获取全量数据"),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选会话 ID"),
    ] = None,
) -> str:
    """查询当前讲师的学习项目清单.

    支持三个视角：我拥有的、协同给我的、我报名的（作为学员参与）。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        url, base_params = _program_list_url_and_params(scope, keywords or "", page, page_size)
    except ValueError as e:
        return _err("INVALID_SCOPE", str(e), next_action="needs_user_input")

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        params = {**base_params, "page": str(p), "size": str(sz)}
        resp = client.get(client.desktop_url(url), params=params)
        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取学习项目列表失败"))

        data = resp.get("data", {})
        page_info = data.get("page_info", {})
        program_list = data.get("list", [])
        total_all = int(page_info.get("list_total_num", 0) or 0)
        return [_format_program_list_item(item) for item in program_list], total_all

    try:
        if fetch_all:
            all_items: list[dict[str, Any]] = []
            total_all = 0
            current_page = 1
            batch_size = 20

            while True:
                items, total_all = _fetch_page(current_page, batch_size)
                all_items.extend(items)

                report_pagination_progress(
                    "tch_list_learning_programs",
                    current_page,
                    len(all_items),
                    total_all,
                    batch_size,
                    is_complete=len(all_items) >= total_all or not items,
                )

                if len(all_items) >= total_all or not items:
                    break
                current_page += 1
                if current_page > 50:
                    report_pagination_progress(
                        "tch_list_learning_programs",
                        current_page,
                        len(all_items),
                        total_all,
                        batch_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

            return _ok(
                data={
                    "scope": scope,
                    "programs": all_items,
                    "total": len(all_items),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": 1,
                        "page_size": len(all_items) if all_items else 0,
                    },
                },
                next_action="proceed",
            )

        items, _ = _fetch_page(page, page_size)
        return _ok(
            data={
                "scope": scope,
                "programs": items,
                "pagination": {
                    "current_page": page,
                    "page_size": page_size,
                },
            },
            next_action="proceed",
        )
    except Exception as e:
        logger.exception("获取学习项目列表失败")
        return _err("LIST_LEARNING_PROGRAMS_FAILED", str(e))


@mcp.tool()
async def tch_create_learning_program(
    title: Annotated[str, Field(description="学习项目标题")],
    desc_plain: Annotated[str, Field(default="", description="纯文本介绍")] = "",
    desc_richtext: Annotated[str, Field(default="", description="富文本介绍（HTML）")] = "",
    cover_image_path: Annotated[str | None, Field(default=None, description="本地封面图路径")] = None,
    bg_image_path: Annotated[str | None, Field(default=None, description="本地背景图路径")] = None,
    cover_image_url: Annotated[str | None, Field(default=None, description="封面图 URL，与 cover_image_path 二选一，URL 优先")] = None,
    bg_image_url: Annotated[str | None, Field(default=None, description="背景图 URL，与 bg_image_path 二选一，URL 优先")] = None,
    tags: Annotated[list[str] | None, Field(default=None, description="标签列表")] = None,
    category_ids: Annotated[list[str] | None, Field(default=None, description="分类 ID 列表")] = None,
    category_names: Annotated[
        list[str] | None,
        Field(default=None, description="分类名称列表，与 category_ids 二选一，名称优先"),
    ] = None,
    start_time: Annotated[str, Field(default="", description="开始时间戳字符串")] = "",
    end_time: Annotated[str, Field(default="", description="结束时间戳字符串")] = "",
    skin_id: Annotated[int, Field(default=1, description="皮肤 ID")] = 1,
    pc_skin_id: Annotated[int, Field(default=1, description="PC 皮肤 ID")] = 1,
    show_banner: Annotated[bool, Field(default=True, description="是否显示 banner")] = True,
    unlock_type: Annotated[int, Field(default=1, description="解锁类型")] = 1,
    show_type: Annotated[int, Field(default=1, description="显示类型")] = 1,
    open_module: Annotated[int, Field(default=1, description="开放模块")] = 1,
    sort: Annotated[str, Field(default="asc", description="排序方式")] = "asc",
    enable_certificate: Annotated[bool, Field(default=False, description="是否启用证书")] = False,
    session_id: Annotated[str | None, Field(default=None, description="可选会话 ID")] = None,
) -> str:
    """创建学习项目（不含课程）.

    返回包含 program_id 的 JSON，可用于后续添加课程。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        builder = ProgramBuilder(client, client.base_url)
        result = builder.create_program(
            title=title,
            desc_plain=desc_plain,
            desc_richtext=desc_richtext,
            cover_path=cover_image_path or "",
            bg_path=bg_image_path or "",
            cover_image_url=cover_image_url or "",
            bg_image_url=bg_image_url or "",
            tags=tags,
            category_ids=category_ids,
            category_names=category_names,
            start_time=start_time,
            end_time=end_time,
            skin_id=skin_id,
            pc_skin_id=pc_skin_id,
            show_banner=show_banner,
            unlock_type=unlock_type,
            show_type=show_type,
            open_module=open_module,
            sort=sort,
            enable_certificate=enable_certificate,
        )
        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="可调用 tch_add_courses_to_learning_program 添加课程",
        )
    except Exception as e:
        logger.exception("创建学习项目失败")
        return _err("CREATE_LEARNING_PROGRAM_FAILED", str(e))


@mcp.tool()
async def tch_add_courses_to_learning_program(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    modules: Annotated[
        list[dict[str, Any]],
        Field(description='模块列表，每项包含 module_title 与 course_ids，例如 [{"module_title": "阶段一", "course_ids": ["7329920"]}]'),
    ],
    session_id: Annotated[str | None, Field(default=None, description="可选会话 ID")] = None,
) -> str:
    """将课程按模块添加到学习项目.

    若 module_id 为空且提供 module_title，则自动创建新模块。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        builder = ProgramBuilder(client, client.base_url)
        result = builder.add_courses(program_id=program_id, modules=modules)
        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="失败课程可单独重试",
        )
    except Exception as e:
        logger.exception("添加课程到学习项目失败")
        return _err("ADD_COURSES_TO_PROGRAM_FAILED", str(e))


@mcp.tool()
async def tch_configure_program_certificate(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    theme_id: Annotated[str, Field(default="", description="证书模板 ID，不填则使用第一个可用模板")] = "",
    text: Annotated[str, Field(default="", description="证书正文")] = "",
    teacher_name: Annotated[str, Field(default="", description="讲师姓名")] = "",
    session_id: Annotated[str | None, Field(default=None, description="可选会话 ID")] = None,
) -> str:
    """配置学习项目证书."""
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")
    try:
        builder = ProgramBuilder(client, client.base_url)
        result = builder.configure_certificate(program_id, theme_id, text, teacher_name)
        return _ok(data=result, next_action="proceed")
    except Exception as e:
        logger.exception("配置证书失败")
        return _err("CONFIGURE_CERTIFICATE_FAILED", str(e))


@mcp.tool()
async def tch_set_program_points_status(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    enabled: Annotated[bool, Field(description="是否开启积分")],
    session_id: Annotated[str | None, Field(default=None, description="可选会话 ID")] = None,
) -> str:
    """开启或关闭学习项目积分."""
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")
    try:
        builder = ProgramBuilder(client, client.base_url)
        result = builder.set_points_status(program_id, enabled)
        return _ok(data=result, next_action="proceed")
    except Exception as e:
        logger.exception("设置积分状态失败")
        return _err("SET_POINTS_STATUS_FAILED", str(e))


@mcp.tool()
async def tch_search_courses_for_program(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    keywords: Annotated[str, Field(default="", description="搜索关键词")] = "",
    creater_name: Annotated[str, Field(default="", description="按创建人筛选")] = "",
    page: Annotated[int, Field(default=1, ge=1, description="页码")] = 1,
    page_size: Annotated[int, Field(default=10, ge=1, le=100, description="每页数量")] = 10,
    session_id: Annotated[str | None, Field(default=None, description="可选会话 ID")] = None,
) -> str:
    """搜索可加入学习项目的课程."""
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")
    try:
        builder = ProgramBuilder(client, client.base_url)
        items, total = builder.search_courses(program_id, keywords, creater_name, page, page_size)
        return _ok(
            data={
                "courses": items,
                "total": total,
                "pagination": {"current_page": page, "page_size": page_size},
            },
            next_action="proceed",
        )
    except Exception as e:
        logger.exception("搜索课程失败")
        return _err("SEARCH_COURSES_FOR_PROGRAM_FAILED", str(e))


@mcp.tool()
async def tch_get_learning_program(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    session_id: Annotated[str | None, Field(default=None, description="可选会话 ID")] = None,
) -> str:
    """获取学习项目详情.

    返回项目基本信息、模块列表及课程关系，可用于修改前查看当前结构。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        builder = ProgramBuilder(client, client.base_url)
        result = builder.get_program(program_id)
        return _ok(data=result, next_action="proceed")
    except Exception as e:
        logger.exception("获取学习项目详情失败")
        return _err("GET_LEARNING_PROGRAM_FAILED", str(e))


@mcp.tool()
async def tch_update_learning_program(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    title: Annotated[str | None, Field(default=None, description="学习项目标题")] = None,
    desc_plain: Annotated[str | None, Field(default=None, description="纯文本介绍")] = None,
    desc_richtext: Annotated[str | None, Field(default=None, description="富文本介绍（HTML）")] = None,
    cover_image_path: Annotated[str | None, Field(default=None, description="本地封面图路径，传空字符串表示清除")] = None,
    bg_image_path: Annotated[str | None, Field(default=None, description="本地背景图路径，传空字符串表示清除")] = None,
    cover_image_url: Annotated[str | None, Field(default=None, description="封面图 URL，与 cover_image_path 二选一，URL 优先")] = None,
    bg_image_url: Annotated[str | None, Field(default=None, description="背景图 URL，与 bg_image_path 二选一，URL 优先")] = None,
    tags: Annotated[list[str] | None, Field(default=None, description="标签列表")] = None,
    category_ids: Annotated[list[str] | None, Field(default=None, description="分类 ID 列表")] = None,
    category_names: Annotated[
        list[str] | None,
        Field(default=None, description="分类名称列表，与 category_ids 二选一，名称优先"),
    ] = None,
    skin_id: Annotated[int | None, Field(default=None, description="皮肤 ID")] = None,
    pc_skin_id: Annotated[int | None, Field(default=None, description="PC 皮肤 ID")] = None,
    show_banner: Annotated[bool | None, Field(default=None, description="是否显示 banner")] = None,
    unlock_type: Annotated[int | None, Field(default=None, description="解锁类型")] = None,
    show_type: Annotated[int | None, Field(default=None, description="显示类型")] = None,
    open_module: Annotated[int | None, Field(default=None, description="开放模块")] = None,
    sort: Annotated[str | None, Field(default=None, description="排序方式")] = None,
    enable_certificate: Annotated[bool | None, Field(default=None, description="是否启用证书")] = None,
    session_id: Annotated[str | None, Field(default=None, description="可选会话 ID")] = None,
) -> str:
    """修改学习项目基本信息.

    未提供的字段保持原值；需要修改的字段显式传入。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        builder = ProgramBuilder(client, client.base_url)
        result = builder.update_program(
            program_id=program_id,
            title=title,
            desc_plain=desc_plain,
            desc_richtext=desc_richtext,
            cover_path=cover_image_path,
            bg_path=bg_image_path,
            cover_image_url=cover_image_url,
            bg_image_url=bg_image_url,
            tags=tags,
            category_ids=category_ids,
            category_names=category_names,
            skin_id=skin_id,
            pc_skin_id=pc_skin_id,
            show_banner=show_banner,
            unlock_type=unlock_type,
            show_type=show_type,
            open_module=open_module,
            sort=sort,
            enable_certificate=enable_certificate,
        )
        return _ok(data=result, next_action="proceed")
    except Exception as e:
        logger.exception("修改学习项目失败")
        return _err("UPDATE_LEARNING_PROGRAM_FAILED", str(e))


@mcp.tool()
async def tch_update_learning_program_modules(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    modules: Annotated[
        list[dict[str, Any]],
        Field(description='模块列表，每项包含 module_id 与可修改字段，例如 [{"module_id": "197797", "module_title": "新标题", "module_desc_richtext": "<p>描述</p>", "group_list": [...]}]'),
    ],
    session_id: Annotated[str | None, Field(default=None, description="可选会话 ID")] = None,
) -> str:
    """修改学习项目的模块信息.

    可修改模块标题、模块描述、模块富文本描述、模块内课程顺序及是否必修。
    group_list 中的 id 为模块课程关系 ID（module_group_id）。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        builder = ProgramBuilder(client, client.base_url)
        result = builder.update_modules(program_id=program_id, modules=modules)
        return _ok(data=result, next_action="proceed")
    except Exception as e:
        logger.exception("修改学习项目模块失败")
        return _err("UPDATE_LEARNING_PROGRAM_MODULES_FAILED", str(e))


@mcp.tool()
async def tch_remove_courses_from_learning_program(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    module_group_ids: Annotated[
        list[str],
        Field(description="模块课程关系 ID 列表（来自 group_list 中的 id）"),
    ],
    session_id: Annotated[str | None, Field(default=None, description="可选会话 ID")] = None,
) -> str:
    """从学习项目中移除课程.

    module_group_ids 可通过 tch_get_learning_program 获取模块内课程的 id 字段。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        builder = ProgramBuilder(client, client.base_url)
        result = builder.remove_courses(program_id=program_id, module_group_ids=module_group_ids)
        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="失败项可单独重试",
        )
    except Exception as e:
        logger.exception("从学习项目移除课程失败")
        return _err("REMOVE_COURSES_FROM_PROGRAM_FAILED", str(e))


@mcp.tool()
async def tch_delete_learning_program(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """删除学习项目.

    触发条件：需要删除讲师拥有的学习项目时调用。
    前置依赖：需先调用 tch_login 完成登录。
    副作用：将学习项目移至平台回收站。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 tch_login 登录",
            next_action="retry",
        )

    if not program_id or str(program_id) in ("0", ""):
        return _err(
            error_code="EMPTY_PROGRAM_ID",
            error_message="program_id 不能为空",
            suggested_action="提供有效的学习项目 ID",
        )

    try:
        resp = client.post(
            client.desktop_url("/api/program/deleteprogram"),
            data={"program_id": program_id},
        )

        if resp.get("status") is True or resp.get("error_code") == 0:
            return _ok(
                data={"program_id": program_id, "deleted": True},
                next_action="proceed",
                suggested_action="学习项目已删除",
            )

        return _err(
            error_code="DELETE_LEARNING_PROGRAM_FAILED",
            error_message=resp.get("error", "删除学习项目失败"),
            suggested_action="请确认对该学习项目具有删除权限",
        )
    except Exception as e:
        logger.exception("删除学习项目失败")
        return _err(
            error_code="DELETE_LEARNING_PROGRAM_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def tch_list_program_participants(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    status_filter: Annotated[
        str,
        Field(
            default="all",
            description="学员完成状态筛选：all=全部, completed=已完成, uncompleted=未完成",
        ),
    ] = "all",
    include_disabled: Annotated[
        bool,
        Field(default=True, description="是否包含已禁用账号，默认包含"),
    ] = True,
    page: Annotated[int, Field(default=1, ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[
        int,
        Field(default=20, ge=1, le=100, description="每页数量，默认 20，最大 100"),
    ] = 20,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选会话 ID"),
    ] = None,
) -> str:
    """查询学习项目的学员名单.

    返回学习项目下所有学员的完成状态，支持按完成状态筛选和是否包含已禁用账号。
    结果中的 modules / courses 字段已根据 table_head 动态列深度格式化。
    讲师及以上权限角色可调用。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 tch_login 完成登录后再重试",
            next_action="retry",
        )

    try:
        manager = ProgramStudentManager(client, client.base_url)
        result = manager.list_participants(
            program_id=program_id,
            status_filter=status_filter,
            include_disabled=include_disabled,
            page=page,
            page_size=page_size,
            fetch_all=fetch_all,
        )
        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="如需查看学员在学习任务维度的详情，可调用 tch_list_program_learning_tasks",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_STATUS_FILTER",
            error_message=str(e),
            suggested_action="请使用 all、completed 或 uncompleted",
            next_action="needs_user_input",
        )
    except Exception as e:
        logger.exception("查询学习项目学员名单失败")
        return _err("LIST_PROGRAM_PARTICIPANTS_FAILED", str(e))


@mcp.tool()
async def tch_list_program_learning_tasks(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    status_filter: Annotated[
        str,
        Field(
            default="all",
            description="学员完成状态筛选：all=全部, completed=已完成, uncompleted=未完成",
        ),
    ] = "all",
    include_disabled: Annotated[
        bool,
        Field(default=True, description="是否包含已禁用账号，默认包含"),
    ] = True,
    page: Annotated[int, Field(default=1, ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[
        int,
        Field(default=20, ge=1, le=100, description="每页数量，默认 20，最大 100"),
    ] = 20,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选会话 ID"),
    ] = None,
) -> str:
    """查询学习项目的学习任务学员名单.

    返回被分配给该学习项目作为学习任务的学员列表，支持按完成状态筛选和是否显示禁用账号。
    结果中的 modules / courses 字段已根据 table_head 动态列深度格式化。
    讲师及以上权限角色可调用。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 tch_login 完成登录后再重试",
            next_action="retry",
        )

    try:
        manager = ProgramStudentManager(client, client.base_url)
        result = manager.list_learning_tasks(
            program_id=program_id,
            status_filter=status_filter,
            include_disabled=include_disabled,
            page=page,
            page_size=page_size,
            fetch_all=fetch_all,
        )
        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="如需查看学员在项目维度的完成详情，可调用 tch_list_program_participants",
        )
    except ValueError as e:
        return _err(
            error_code="INVALID_STATUS_FILTER",
            error_message=str(e),
            suggested_action="请使用 all、completed 或 uncompleted",
            next_action="needs_user_input",
        )
    except Exception as e:
        logger.exception("查询学习项目学习任务学员名单失败")
        return _err("LIST_PROGRAM_LEARNING_TASKS_FAILED", str(e))


@mcp.tool()
async def tch_list_course_collaborators(
    group_id: Annotated[str, Field(description="课程 ID")],
    page: Annotated[int, Field(default=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Field(default=20, description="每页数量")] = 20,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """列出课程的协同者.

    返回课程的协同者列表、创建者信息以及分页信息。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        resp = client.get(
            client.desktop_url("/api/cooperation/getall"),
            params={
                "t": str(int(time.time() * 1000)),
                "append_manage_role": "1",
                "obj_id": group_id,
                "obj_type": "group",
                "page": str(page),
                "size": str(page_size),
            },
        )
        ok, data, err = _parse_collaboration_response(resp)
        if not ok:
            return _err("LIST_COLLABORATORS_FAILED", err or "获取协同者列表失败")

        raw_list = data.get("list", []) if isinstance(data, dict) else []
        creator_info = data.get("creator_info", {}) if isinstance(data, dict) else {}
        page_info = data.get("page_info", {}) if isinstance(data, dict) else {}

        collaborators = []
        for item in raw_list:
            collaborators.append({
                "cooperation_info_id": item.get("cooperation_info_id"),
                "teacher_id": item.get("teacher_id"),
                "umu_id": item.get("umu_id"),
                "role_type": item.get("role_type"),
                "role_label": _API_ROLE_TO_LABEL.get(item.get("role_type", ""), item.get("role_type", "")),
                "teacher_name": item.get("teacher_name"),
                "teacher_email": item.get("teacher_email"),
                "cooperator_type": item.get("cooperator_type"),
                "is_manager": item.get("is_manager"),
                "manager_role_type": item.get("manager_role_type"),
            })

        creator = {
            "teacher_id": creator_info.get("teacher_id"),
            "role_type": creator_info.get("role_type"),
            "role_label": _API_ROLE_TO_LABEL.get(creator_info.get("role_type", ""), creator_info.get("role_type", "")),
            "teacher_name": creator_info.get("teacher_name"),
            "teacher_email": creator_info.get("teacher_email"),
        } if creator_info else None

        return _ok(
            data={
                "collaborators": collaborators,
                "creator": creator,
                "pagination": page_info,
            },
            next_action="proceed",
            suggested_action="如需邀请/调整/删除协同者，可调用对应工具",
        )
    except Exception as e:
        logger.exception("列出课程协同者失败")
        return _err("LIST_COLLABORATORS_FAILED", str(e))


@mcp.tool()
async def tch_list_course_learning_tasks(
    group_id: Annotated[str, Field(description="课程 ID（group_id）")],
    status_filter: Annotated[
        str,
        Field(
            default="all",
            description="学员完成状态筛选：all=全部, completed=已完成, uncompleted=未完成",
        ),
    ] = "all",
    include_disabled: Annotated[
        bool,
        Field(default=True, description="是否包含已禁用账号，默认包含"),
    ] = True,
    page: Annotated[int, Field(default=1, ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[
        int,
        Field(default=20, ge=1, le=100, description="每页数量，默认 20，最大 100"),
    ] = 20,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """查询课程的学习任务分配学员清单.

    返回被分配给该课程作为学习任务的学员列表，支持按完成状态筛选和是否显示禁用账号。
    讲师及以上权限角色可调用。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 tch_login 完成登录后再重试",
            next_action="retry",
        )

    status_map = {"all": "0", "completed": "1", "uncompleted": "2"}
    if status_filter not in status_map:
        return _err(
            error_code="INVALID_STATUS_FILTER",
            error_message=f"status_filter 必须是 all/completed/uncompleted 之一，收到: {status_filter}",
            suggested_action="请使用 all、completed 或 uncompleted",
            next_action="needs_user_input",
        )

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int, dict[str, Any], dict[str, Any]]:
        resp = client.get(
            client.desktop_url("/api/studentManage/getstudenttasklist"),
            params={
                "t": str(int(time.time() * 1000)),
                "group_id": group_id,
                "type": status_map[status_filter],
                "filter_disabled_user": "0" if include_disabled else "1",
                "is_enroll": "0",
                "is_require": "0",
                "only_enterprise_on_job": "0",
                "student_only": "0",
                "page": str(p),
                "size": str(sz),
                "sort_field": "1",
                "sort_type": "2",
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取学习任务学员清单失败"))

        data = resp.get("data", {})
        page_info = data.get("table_body", {}).get("page_info", {})
        student_list = data.get("table_body", {}).get("list", [])

        formatted: list[dict[str, Any]] = []
        for item in student_list:
            formatted.append({
                "student_id": item.get("student_id", ""),
                "umu_id": item.get("umu_id", ""),
                "user_name": item.get("user_name", ""),
                "avatar": item.get("avatar", ""),
                "task_count": item.get("task_count", 0),
                "complete_num": item.get("complete_num", 0),
                "complete_rate": item.get("complete_rate", 0),
                "is_assign": item.get("is_assign", 0),
                "assign_time": item.get("assign_time", 0),
                "last_assign_time": item.get("last_assign_time", 0),
                "complete_time": item.get("complete_time", 0),
                "first_learning_time": item.get("first_learning_time", 0),
                "last_learning_time": item.get("last_learning_time", 0),
                "due_time": item.get("due_time", 0),
            })

        total_all = int(page_info.get("list_total_num", 0) or 0)
        return formatted, total_all, data.get("data_count", {}), page_info

    def _build_summary(data_count: dict[str, Any]) -> dict[str, Any]:
        return {
            "total": data_count.get("total_num", 0),
            "completed": data_count.get("complete_num", 0),
            "uncompleted": data_count.get("uncomplete_num", 0),
            "completion_rate": data_count.get("complete_rate", 0),
            "has_learning_task": bool(data_count.get("exist_learning_task", 0)),
        }

    try:
        if fetch_all:
            batch_size = 50
            all_students: list[dict[str, Any]] = []
            total_all = 0
            current_page = 1
            latest_data_count: dict[str, Any] = {}

            while True:
                page_items, total_all, data_count, _ = _fetch_page(current_page, batch_size)
                all_students.extend(page_items)
                latest_data_count = data_count

                report_pagination_progress(
                    "tch_list_course_learning_tasks",
                    current_page,
                    len(all_students),
                    total_all,
                    batch_size,
                )

                if not page_items or len(all_students) >= total_all:
                    report_pagination_progress(
                        "tch_list_course_learning_tasks",
                        current_page,
                        len(all_students),
                        total_all,
                        batch_size,
                        is_complete=True,
                    )
                    break

                if current_page >= 50:
                    report_pagination_progress(
                        "tch_list_course_learning_tasks",
                        current_page,
                        len(all_students),
                        total_all,
                        batch_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

                current_page += 1

            summary = _build_summary(latest_data_count)
            return _ok(
                data={
                    "summary": summary,
                    "students": all_students,
                    "pagination": {
                        "total_all": total_all,
                        "current_page": current_page,
                        "page_size": batch_size,
                    },
                },
                next_action="proceed",
                suggested_action="如需查看某位学员的详细学习记录，可调用相关工具",
            )

        students, _, data_count, page_info = _fetch_page(page, page_size)
        summary = _build_summary(data_count)
        return _ok(
            data={
                "summary": summary,
                "students": students,
                "pagination": {
                    "total": int(page_info.get("list_total_num", 0) or 0),
                    "total_pages": int(page_info.get("total_page_num", 0) or 0),
                    "current_page": int(page_info.get("current_page", page)),
                    "page_size": int(page_info.get("size", page_size)),
                },
            },
            next_action="proceed",
            suggested_action="如需获取全量数据，可设置 fetch_all=True",
        )
    except Exception as e:
        logger.exception("获取课程学习任务学员清单失败")
        return _err(
            error_code="LIST_COURSE_LEARNING_TASKS_ERROR",
            error_message=str(e),
            suggested_action="请检查 group_id 是否正确，或稍后重试",
        )


@mcp.tool()
async def tch_list_course_participants(
    group_id: Annotated[str, Field(description="课程 ID（group_id）")],
    status_filter: Annotated[
        str,
        Field(
            default="all",
            description="学员完成状态筛选：all=全部, completed=必修完成, uncompleted=必修未完成",
        ),
    ] = "all",
    include_disabled: Annotated[
        bool,
        Field(default=True, description="是否包含已禁用账号，默认包含"),
    ] = True,
    page: Annotated[int, Field(default=1, ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[
        int,
        Field(default=20, ge=1, le=100, description="每页数量，默认 20，最大 100"),
    ] = 20,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """查询指定课程的学员参与者名单.

    返回课程的参训学员列表，支持按必修完成状态筛选和是否显示禁用账号。
    返回结果包含每位学员每个必修/选修小节的完成状态、积分、得分等明细。
    讲师及以上权限角色可调用。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 tch_login 完成登录后再重试",
            next_action="retry",
        )

    status_map = {"all": "0", "completed": "1", "uncompleted": "2"}
    if status_filter not in status_map:
        return _err(
            error_code="INVALID_STATUS_FILTER",
            error_message=f"status_filter 必须是 all/completed/uncompleted 之一，收到: {status_filter}",
            suggested_action="请使用 all、completed 或 uncompleted",
            next_action="needs_user_input",
        )

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int, dict[str, Any], dict[str, Any]]:
        resp = client.get(
            client.desktop_url("/api/studentManage/getstudentlist"),
            params={
                "t": str(int(time.time() * 1000)),
                "group_id": group_id,
                "type": status_map[status_filter],
                "filter_disabled_user": "0" if include_disabled else "1",
                "is_enroll": "0",
                "is_require": "0",
                "only_enterprise_on_job": "0",
                "student_only": "0",
                "page": str(p),
                "size": str(sz),
                "sort_field": "1",
                "sort_type": "2",
                "v": "1",
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取课程学员参与者名单失败"))

        data = resp.get("data", {})
        page_info = data.get("table_body", {}).get("page_info", {})
        student_list = data.get("table_body", {}).get("list", [])

        total_all = int(page_info.get("list_total_num", 0) or 0)
        return student_list, total_all, data.get("data_count", {}), page_info

    def _build_summary(data_count: dict[str, Any]) -> dict[str, Any]:
        return {
            "total": data_count.get("total_num", 0),
            "completed": data_count.get("complete_num", 0),
            "uncompleted": data_count.get("uncomplete_num", 0),
            "completion_rate": data_count.get("complete_rate", 0),
        }

    try:
        if fetch_all:
            batch_size = 50
            all_students: list[dict[str, Any]] = []
            total_all = 0
            current_page = 1
            latest_data_count: dict[str, Any] = {}

            while True:
                page_items, total_all, data_count, _ = _fetch_page(current_page, batch_size)
                all_students.extend(page_items)
                latest_data_count = data_count

                report_pagination_progress(
                    "tch_list_course_participants",
                    current_page,
                    len(all_students),
                    total_all,
                    batch_size,
                )

                if not page_items or len(all_students) >= total_all:
                    report_pagination_progress(
                        "tch_list_course_participants",
                        current_page,
                        len(all_students),
                        total_all,
                        batch_size,
                        is_complete=True,
                    )
                    break

                if current_page >= 50:
                    report_pagination_progress(
                        "tch_list_course_participants",
                        current_page,
                        len(all_students),
                        total_all,
                        batch_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

                current_page += 1

            summary = _build_summary(latest_data_count)
            return _ok(
                data={
                    "summary": summary,
                    "students": all_students,
                    "pagination": {
                        "total_all": total_all,
                        "current_page": current_page,
                        "page_size": batch_size,
                    },
                },
                next_action="proceed",
                suggested_action="如需查看学员学习时长，可调用 tch_list_course_learning_durations",
            )

        students, _, data_count, page_info = _fetch_page(page, page_size)
        summary = _build_summary(data_count)
        return _ok(
            data={
                "summary": summary,
                "students": students,
                "pagination": {
                    "total": int(page_info.get("list_total_num", 0) or 0),
                    "total_pages": int(page_info.get("total_page_num", 0) or 0),
                    "current_page": int(page_info.get("current_page", page)),
                    "page_size": int(page_info.get("size", page_size)),
                },
            },
            next_action="proceed",
            suggested_action="如需获取全量数据，可设置 fetch_all=True",
        )
    except Exception as e:
        logger.exception("获取课程学员参与者名单失败")
        return _err(
            error_code="LIST_COURSE_PARTICIPANTS_ERROR",
            error_message=str(e),
            suggested_action="请检查 group_id 是否正确，或稍后重试",
        )


@mcp.tool()
async def tch_list_course_learning_durations(
    group_id: Annotated[str, Field(description="课程 ID（group_id）")],
    status_filter: Annotated[
        str,
        Field(
            default="all",
            description="学员完成状态筛选：all=全部, completed=必修完成, uncompleted=必修未完成",
        ),
    ] = "all",
    include_disabled: Annotated[
        bool,
        Field(default=True, description="是否包含已禁用账号，默认包含"),
    ] = True,
    page: Annotated[int, Field(default=1, ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[
        int,
        Field(default=20, ge=1, le=100, description="每页数量，默认 20，最大 100"),
    ] = 20,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """查询指定课程的学员学习时长名单.

    返回课程的参训学员学习时长列表，支持按必修完成状态筛选和是否显示禁用账号。
    返回结果包含每位学员的课程总学习时长，以及每个必修/选修小节的学习时长、首次/末次学习时间等明细。
    讲师及以上权限角色可调用。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 tch_login 完成登录后再重试",
            next_action="retry",
        )

    status_map = {"all": "0", "completed": "1", "uncompleted": "2"}
    if status_filter not in status_map:
        return _err(
            error_code="INVALID_STATUS_FILTER",
            error_message=f"status_filter 必须是 all/completed/uncompleted 之一，收到: {status_filter}",
            suggested_action="请使用 all、completed 或 uncompleted",
            next_action="needs_user_input",
        )

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int, dict[str, Any], dict[str, Any]]:
        resp = client.get(
            client.desktop_url("/api/studentManage/grouplearningtimelist"),
            params={
                "t": str(int(time.time() * 1000)),
                "group_id": group_id,
                "type": status_map[status_filter],
                "filter_disabled_user": "0" if include_disabled else "1",
                "is_enroll": "0",
                "is_require": "0",
                "only_enterprise_on_job": "0",
                "student_only": "0",
                "page": str(p),
                "size": str(sz),
                "sort_field": "1",
                "sort_type": "2",
                "v": "2",
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取课程学员学习时长名单失败"))

        data = resp.get("data", {})
        page_info = data.get("table_body", {}).get("page_info", {})
        student_list = data.get("table_body", {}).get("list", [])

        total_all = int(page_info.get("list_total_num", 0) or 0)
        return student_list, total_all, data.get("data_count", {}), page_info

    def _build_summary(data_count: dict[str, Any]) -> dict[str, Any]:
        return {
            "total": data_count.get("total_num", 0),
            "completed": data_count.get("complete_num", 0),
            "uncompleted": data_count.get("uncomplete_num", 0),
            "completion_rate": data_count.get("complete_rate", 0),
            "avg_vlt": data_count.get("avg_vlt", ""),
        }

    try:
        if fetch_all:
            batch_size = 50
            all_students: list[dict[str, Any]] = []
            total_all = 0
            current_page = 1
            latest_data_count: dict[str, Any] = {}

            while True:
                page_items, total_all, data_count, _ = _fetch_page(current_page, batch_size)
                all_students.extend(page_items)
                latest_data_count = data_count

                report_pagination_progress(
                    "tch_list_course_learning_durations",
                    current_page,
                    len(all_students),
                    total_all,
                    batch_size,
                )

                if not page_items or len(all_students) >= total_all:
                    report_pagination_progress(
                        "tch_list_course_learning_durations",
                        current_page,
                        len(all_students),
                        total_all,
                        batch_size,
                        is_complete=True,
                    )
                    break

                if current_page >= 50:
                    report_pagination_progress(
                        "tch_list_course_learning_durations",
                        current_page,
                        len(all_students),
                        total_all,
                        batch_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

                current_page += 1

            summary = _build_summary(latest_data_count)
            return _ok(
                data={
                    "summary": summary,
                    "students": all_students,
                    "pagination": {
                        "total_all": total_all,
                        "current_page": current_page,
                        "page_size": batch_size,
                    },
                },
                next_action="proceed",
                suggested_action="如需查看学员完成状态，可调用 tch_list_course_participants",
            )

        students, _, data_count, page_info = _fetch_page(page, page_size)
        summary = _build_summary(data_count)
        return _ok(
            data={
                "summary": summary,
                "students": students,
                "pagination": {
                    "total": int(page_info.get("list_total_num", 0) or 0),
                    "total_pages": int(page_info.get("total_page_num", 0) or 0),
                    "current_page": int(page_info.get("current_page", page)),
                    "page_size": int(page_info.get("size", page_size)),
                },
            },
            next_action="proceed",
            suggested_action="如需获取全量数据，可设置 fetch_all=True",
        )
    except Exception as e:
        logger.exception("获取课程学员学习时长名单失败")
        return _err(
            error_code="LIST_COURSE_LEARNING_DURATIONS_ERROR",
            error_message=str(e),
            suggested_action="请检查 group_id 是否正确，或稍后重试",
        )


@mcp.tool()
async def tch_search_collaborator_accounts(
    group_id: Annotated[str, Field(description="课程 ID")],
    keyword: Annotated[str, Field(description="查询关键词：邮箱、姓名、用户名或手机号")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """搜索可设置为课程协同者的账号.

    仅返回角色为讲师、学习负责人、子管理员或管理员的账号。学员角色不会被返回。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        ok, accounts, err = _search_collaborator_account(client, group_id, keyword)
        if not ok:
            return _err("SEARCH_COLLABORATOR_FAILED", err or "搜索可协同账号失败")

        normalized = [
            {
                "id": acc.get("id"),
                "umu_id": acc.get("umu_id"),
                "student_id": acc.get("student_id"),
                "account": acc.get("account"),
                "account_type": acc.get("account_type"),
                "user_name": acc.get("user_name"),
                "email": acc.get("email"),
                "phone": acc.get("phone"),
                "login_name": acc.get("login_name"),
            }
            for acc in accounts
        ]

        return _ok(
            data={"accounts": normalized, "count": len(normalized)},
            next_action="proceed",
            suggested_action="使用 account/umu_id/id 调用 tch_invite_course_collaborator",
        )
    except Exception as e:
        logger.exception("搜索可协同账号失败")
        return _err("SEARCH_COLLABORATOR_FAILED", str(e))



def _add_or_update_cooperator(
    client: UMUClient,
    group_id: str,
    account: dict[str, Any],
    api_role: str,
) -> tuple[bool, str]:
    """调用 addcooperators 添加或更新协同权限."""
    payload = [
        {
            "type": 1,
            "role_type": api_role,
            "account": account.get("account") or account.get("email") or account.get("phone") or "",
            "account_type": account.get("account_type", "user"),
            "umu_id": account.get("umu_id") or account.get("id") or "",
        }
    ]
    resp = client.post(
        client.desktop_url("/api/cooperation/addcooperators"),
        data={
            "obj_id": group_id,
            "obj_type": "group",
            "accounts": json.dumps(payload, ensure_ascii=False),
        },
    )
    ok, _, err = _parse_collaboration_response(resp)
    if not ok:
        return False, err or "添加/更新协同权限失败"
    return True, ""


@mcp.tool()
async def tch_invite_course_collaborator(
    group_id: Annotated[str, Field(description="课程 ID")],
    keyword: Annotated[str, Field(description="被邀请者查询关键词：邮箱、姓名、用户名或手机号")],
    role_type: Annotated[
        str,
        Field(description="协同权限：editor（编辑者）/ operator（运营者）/ viewer（查看者）"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """邀请用户成为课程协同者，或调整已有协同者权限.

    工具内部会先搜索账号，只有唯一匹配时才会执行邀请。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    api_role = _map_role_to_api(role_type)
    if api_role is None:
        return _err(
            "INVALID_ROLE_TYPE",
            f"不支持的权限类型 '{role_type}'，请选择 editor/operator/viewer",
            next_action="needs_user_input",
        )

    try:
        ok, accounts, err = _search_collaborator_account(client, group_id, keyword)
        if not ok:
            return _err("SEARCH_COLLABORATOR_FAILED", err or "搜索账号失败")

        account, err = _find_unique_account(accounts, keyword)
        if account is None:
            return _err("AMBIGUOUS_ACCOUNT", err, next_action="needs_user_input")

        add_ok, add_err = _add_or_update_cooperator(client, group_id, account, api_role)
        if not add_ok:
            return _err("INVITE_COLLABORATOR_FAILED", add_err)

        return _ok(
            data={
                "account": account.get("account") or account.get("email"),
                "user_name": account.get("user_name"),
                "role_type": api_role,
                "role_label": _API_ROLE_TO_LABEL.get(api_role, api_role),
                "group_id": group_id,
            },
            next_action="proceed",
            suggested_action="如需确认权限，可调用 tch_list_course_collaborators",
        )
    except Exception as e:
        logger.exception("邀请课程协同者失败")
        return _err("INVITE_COLLABORATOR_FAILED", str(e))


@mcp.tool()
async def tch_update_collaborator_role(
    group_id: Annotated[str, Field(description="课程 ID")],
    cooperation_info_id: Annotated[str, Field(description="协同关系 ID，从 tch_list_course_collaborators 获取")],
    role_type: Annotated[
        str,
        Field(description="新的协同权限：editor（编辑者）/ operator（运营者）/ viewer（查看者）"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """调整已有协同者的权限."""
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    api_role = _map_role_to_api(role_type)
    if api_role is None:
        return _err(
            "INVALID_ROLE_TYPE",
            f"不支持的权限类型 '{role_type}'，请选择 editor/operator/viewer",
            next_action="needs_user_input",
        )

    try:
        resp = client.get(
            client.desktop_url("/api/cooperation/getall"),
            params={
                "t": str(int(time.time() * 1000)),
                "append_manage_role": "1",
                "obj_id": group_id,
                "obj_type": "group",
                "page": "1",
                "size": "20",
            },
        )
        ok, data, err = _parse_collaboration_response(resp)
        if not ok:
            return _err("LIST_COLLABORATORS_FAILED", err or "获取协同者列表失败")

        raw_list = data.get("list", []) if isinstance(data, dict) else []
        target = next(
            (item for item in raw_list if str(item.get("cooperation_info_id")) == str(cooperation_info_id)),
            None,
        )
        if target is None:
            return _err(
                "COLLABORATOR_NOT_FOUND",
                f"未找到协同关系 ID: {cooperation_info_id}",
                next_action="needs_user_input",
            )

        add_ok, add_err = _add_or_update_cooperator(client, group_id, target, api_role)
        if not add_ok:
            return _err("UPDATE_COLLABORATOR_ROLE_FAILED", add_err)

        return _ok(
            data={
                "cooperation_info_id": cooperation_info_id,
                "teacher_id": target.get("teacher_id"),
                "user_name": target.get("teacher_name"),
                "new_role_type": api_role,
                "new_role_label": _API_ROLE_TO_LABEL.get(api_role, api_role),
                "group_id": group_id,
            },
            next_action="proceed",
        )
    except Exception as e:
        logger.exception("更新协同者权限失败")
        return _err("UPDATE_COLLABORATOR_ROLE_FAILED", str(e))


@mcp.tool()
async def tch_remove_course_collaborator(
    group_id: Annotated[str, Field(description="课程 ID")],
    cooperation_info_id: Annotated[str, Field(description="协同关系 ID，从 tch_list_course_collaborators 获取")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """删除课程的协同者."""
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        resp = client.post(
            client.desktop_url("/api/cooperation/del"),
            data={"cooperation_info_ids": str(cooperation_info_id)},
        )
        ok, data, err = _parse_collaboration_response(resp)
        if not ok:
            return _err("REMOVE_COLLABORATOR_FAILED", err or "删除协同者失败")

        result = data.get("result") if isinstance(data, dict) else None
        return _ok(
            data={
                "cooperation_info_id": cooperation_info_id,
                "group_id": group_id,
                "result": result,
            },
            next_action="proceed",
            suggested_action="如需确认，可调用 tch_list_course_collaborators",
        )
    except Exception as e:
        logger.exception("删除课程协同者失败")
        return _err("REMOVE_COLLABORATOR_FAILED", str(e))


@mcp.tool()
async def tch_transfer_course_owner(
    group_id: Annotated[str, Field(description="课程 ID")],
    keyword: Annotated[str, Field(description="新拥有者查询关键词：邮箱、姓名、用户名或手机号")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """将课程拥有权转让给其他用户.

    工具内部会先搜索账号，只有唯一匹配时才会执行转让。
    注意：转让后当前用户将失去拥有者权限，请谨慎操作。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        ok, accounts, err = _search_collaborator_account(client, group_id, keyword)
        if not ok:
            return _err("SEARCH_COLLABORATOR_FAILED", err or "搜索账号失败")

        account, err = _find_unique_account(accounts, keyword)
        if account is None:
            return _err("AMBIGUOUS_ACCOUNT", err, next_action="needs_user_input")

        teacher_id = account.get("id")
        if not teacher_id:
            return _err("TRANSFER_OWNER_FAILED", "账号信息缺少 teacher_id，无法转让")

        resp = client.post(
            client.desktop_url("/uapi/v1/cooperation/permission-transfer"),
            data={
                "obj_id": group_id,
                "obj_type": "group",
                "transferred_teacher_id": str(teacher_id),
            },
        )
        ok, data, err = _parse_collaboration_response(resp)
        if not ok:
            return _err("TRANSFER_OWNER_FAILED", err or "转让课程拥有者失败")

        return _ok(
            data={
                "group_id": group_id,
                "new_owner_id": teacher_id,
                "new_owner_name": account.get("user_name"),
                "new_owner_account": account.get("account") or account.get("email"),
                "status": data.get("status") if isinstance(data, dict) else None,
            },
            next_action="proceed",
            suggested_action="课程拥有权已转让，当前用户需重新确认权限",
        )
    except Exception as e:
        logger.exception("转让课程拥有者失败")
        return _err("TRANSFER_OWNER_FAILED", str(e))


# ---------------------------------------------------------------------------
# Tools: 课程访问权限
# ---------------------------------------------------------------------------


@mcp.tool()
async def tch_set_course_access_permission(
    group_id: Annotated[str, Field(description="课程 ID")],
    access_permission: Annotated[
        int,
        Field(
            description="课程访问权限：0=关闭（任何人不可见），2=企业内公开，3=指定账户/班级/部门/分组可见",
            ge=0,
            le=3,
        ),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """设置课程的整体访问权限.

    支持三种状态：
    - 0：关闭，任何人均不可访问
    - 2：企业内公开，企业内成员均可访问
    - 3：指定账户/班级/部门/分组可见，需要再调用 tch_add_course_access_accounts 添加可见对象

    设置时会同步更新课程下所有小节的权限（update_session_permission=1）。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        detail = _set_obj_access_permission(
            client, group_id, "group", access_permission, update_session_permission=True
        )
        return _ok(
            data={
                "group_id": str(group_id),
                "access_permission": access_permission,
                "permission_text": _permission_text(access_permission),
                "update_session_permission": True,
                "detail": detail,
            },
            next_action="proceed",
            suggested_action=(
                "access_permission=3 时，请继续调用 tch_add_course_access_accounts 添加可见账户"
                if access_permission == 3 else ""
            ),
        )
    except Exception as e:
        logger.exception("设置课程访问权限失败")
        return _err("SET_COURSE_ACCESS_PERMISSION_FAILED", str(e))


@mcp.tool()
async def tch_get_course_access_permission(
    group_id: Annotated[str, Field(description="课程 ID")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取课程当前的访问权限设置.

    返回当前课程的 access_permission（0=关闭，2=企业内公开，3=指定账户/班级/部门/分组），
    以及该课程可设置的所有权限选项。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        selected_int, options, detail = _get_obj_access_permission(client, group_id, "group")
        permission_text = _permission_text(selected_int) if selected_int >= 0 else "未知"

        return _ok(
            data={
                "group_id": str(group_id),
                "access_permission": selected_int,
                "permission_text": permission_text,
                "permission_options": options,
                "detail": detail,
            },
            next_action="proceed",
        )
    except Exception as e:
        logger.exception("获取课程访问权限失败")
        return _err("GET_COURSE_ACCESS_PERMISSION_FAILED", str(e))


@mcp.tool()
async def tch_get_course_access_list(
    group_id: Annotated[str, Field(description="课程 ID")],
    page: Annotated[int, Field(default=1, description="页码", ge=1)] = 1,
    size: Annotated[int, Field(default=20, description="每页数量", ge=1, le=100)] = 20,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取课程当前已授权的访问列表.

    当课程 access_permission 为 3 时，返回已授权的账户/班级/部门/分组列表。
    返回结果中的 account_type 字段标识对象类型：user/class/department/group。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        items, page_info = _get_obj_access_list(client, group_id, "group", page, size)
        return _ok(
            data={
                "group_id": str(group_id),
                "page": page,
                "size": size,
                "page_info": page_info,
                "list": items,
                "total": page_info.get("list_total_num", len(items)),
            },
            next_action="proceed",
        )
    except Exception as e:
        logger.exception("获取课程访问列表失败")
        return _err("GET_COURSE_ACCESS_LIST_FAILED", str(e))


@mcp.tool()
async def tch_search_access_accounts(
    group_id: Annotated[str, Field(description="课程 ID")],
    keyword: Annotated[str, Field(description="搜索关键词：账户邮箱、姓名、班级名称、部门名称或分组名称，支持模糊匹配")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """搜索可授权访问课程的账户、班级、部门或分组.

    返回结果中 account_type 为 user 表示账户，class 表示班级，
    department 表示部门，group 表示分组。
    建议优先使用完整邮箱搜索账户，以提高匹配准确度。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        ok, accounts, err = _search_access_permission_account(client, group_id, "group", keyword)
        if not ok:
            return _err("SEARCH_ACCESS_ACCOUNTS_FAILED", err or "搜索可授权账户失败")

        return _ok(
            data={
                "group_id": str(group_id),
                "keyword": keyword,
                "accounts": [_format_access_account(acc) for acc in accounts],
                "total": len(accounts),
            },
            next_action="proceed",
            suggested_action="请从结果中选择目标账户/班级，调用 tch_add_course_access_accounts 添加权限",
        )
    except Exception as e:
        logger.exception("搜索可授权账户失败")
        return _err("SEARCH_ACCESS_ACCOUNTS_FAILED", str(e))


@mcp.tool()
async def tch_add_course_access_accounts(
    group_id: Annotated[str, Field(description="课程 ID")],
    accounts: Annotated[
        list[dict[str, Any]],
        Field(
            description="要添加的账户/班级/部门/分组列表，每个元素需包含 account、account_type(user/class/department/group)、id，"
                        "class 类型还需包含 class_id，department 类型还需包含 department_id，"
                        "group 类型还需包含 user_group_id（可调用 tch_search_access_accounts 获取）",
        ),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """为课程设置指定账户/班级/部门/分组可见权限.

    前置条件：课程 access_permission 需为 3（指定账户可见）。
    如当前不是该状态，工具会先自动调用 setgrouppermission 设置为 3，再添加对象。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    if not accounts:
        return _err(
            "NO_ACCOUNTS_PROVIDED",
            "未提供要添加的账户/班级",
            suggested_action="请先调用 tch_search_access_accounts 查询目标",
            next_action="needs_user_input",
        )

    try:
        detail = _add_obj_access_accounts(client, group_id, "group", accounts, update_session_permission=True)
        return _ok(
            data={
                "group_id": str(group_id),
                "added": len(accounts),
                "accounts": accounts,
                "detail": detail,
            },
            next_action="proceed",
            suggested_action="可调用 tch_search_access_accounts 或查看 UMU 后台确认权限已生效",
        )
    except Exception as e:
        logger.exception("添加课程指定账户失败")
        return _err("ADD_COURSE_ACCESS_ACCOUNTS_FAILED", str(e))


@mcp.tool()
async def tch_remove_course_access_accounts(
    group_id: Annotated[str, Field(description="课程 ID")],
    accounts: Annotated[
        list[dict[str, Any]],
        Field(
            description="要移除的账户/班级/部门/分组列表，每个元素需包含 account、account_type(user/class/department/group)、id，"
                        "class 类型还需包含 class_id，department 类型还需包含 department_id，"
                        "group 类型还需包含 user_group_id",
        ),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """移除课程的指定账户/班级/部门/分组访问权限."""
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    if not accounts:
        return _err(
            "NO_ACCOUNTS_PROVIDED",
            "未提供要移除的账户/班级",
            next_action="needs_user_input",
        )

    try:
        detail = _remove_obj_access_accounts(client, group_id, "group", accounts, update_session_permission=True)
        return _ok(
            data={
                "group_id": str(group_id),
                "removed": len(accounts),
                "accounts": accounts,
                "detail": detail,
            },
            next_action="proceed",
        )
    except Exception as e:
        logger.exception("移除课程指定账户失败")
        return _err("REMOVE_COURSE_ACCESS_ACCOUNTS_FAILED", str(e))


@mcp.tool()
async def tch_cancel_all_assigned_permissions(
    group_id: Annotated[str, Field(description="课程 ID")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """取消课程的所有指定访问权限.

    调用后会清空课程的指定账户/班级列表，常用于将课程还原为企业内公开前的清理。
    如需同时设置为企业内公开，请在调用后继续调用 tch_set_course_access_permission。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        detail = _cancel_all_assigned_permissions(client, group_id, "group")
        return _ok(
            data={
                "group_id": str(group_id),
                "detail": detail,
            },
            next_action="proceed",
            suggested_action="如需还原为企业内公开，请调用 tch_set_course_access_permission(group_id, access_permission=2)",
        )
    except Exception as e:
        logger.exception("取消课程指定权限失败")
        return _err("CANCEL_ASSIGNED_PERMISSIONS_FAILED", str(e))


# ---------------------------------------------------------------------------
# 课程自动关闭
# ---------------------------------------------------------------------------


@mcp.tool()
async def tch_get_course_auto_close(
    group_id: Annotated[str, Field(description="课程 ID")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """查询课程自动关闭时间设置.

    返回当前课程的 open_time、close_time、open_access_permission。
    close_time 为 0 表示未设置自动关闭。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        resp = client.get(
            client.desktop_url("/uapi/v1/course/get-timing-switch"),
            params={"course_id": str(group_id)},
        )
        if not isinstance(resp, dict) or resp.get("error_code") != 0:
            return _err(
                "GET_COURSE_AUTO_CLOSE_FAILED",
                resp.get("error_message") or "查询课程自动关闭时间失败",
            )

        data = resp.get("data", {})
        return _ok(
            data={
                "group_id": str(group_id),
                "open_time": data.get("open_time", 0),
                "close_time": data.get("close_time", 0),
                "open_access_permission": data.get("open_access_permission", 0),
            },
            next_action="proceed",
        )
    except Exception as e:
        logger.exception("查询课程自动关闭时间失败")
        return _err("GET_COURSE_AUTO_CLOSE_FAILED", str(e))


@mcp.tool()
async def tch_set_course_auto_close(
    group_id: Annotated[str, Field(description="课程 ID")],
    close_time: Annotated[
        str,
        Field(
            description="自动关闭时间，支持格式如 2026-06-30 10:00、2026-06-30T10:00:00、2026年6月30日10点"
        ),
    ],
    custom_tips: Annotated[
        str | None,
        Field(
            default=None,
            description="自定义提示文本；若提供则直接作为 accessPermissionTips，忽略 close_time 的默认格式化",
        ),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """设置课程自动关闭时间.

    会先调用 /uapi/v1/course/set-timing-switch 设置真实的关闭时间戳，
    再保存 group_setup.accessPermissionTips 作为前端展示文案。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        parsed = _parse_auto_close_time(close_time)
        close_timestamp = int(parsed.timestamp())
    except ValueError as e:
        return _err("INVALID_CLOSE_TIME", str(e), next_action="needs_user_input")

    try:
        resp = client.post(
            client.desktop_url("/uapi/v1/course/set-timing-switch"),
            data={
                "course_id": str(group_id),
                "open_time": "0",
                "close_time": str(close_timestamp),
            },
        )
        if not isinstance(resp, dict) or resp.get("error_code") != 0:
            return _err(
                "SET_COURSE_AUTO_CLOSE_FAILED",
                resp.get("error_message") or "设置课程自动关闭时间失败",
            )

        tips = custom_tips.strip() if custom_tips else _format_auto_close_tips(close_time)
        group_setup = {
            "accessPermissionTips": tips,
            "access_permission_tips": tips,
            "enable_mini_program": 0,
            "learn_within_mini_program": 0,
        }
        setup_resp = client.post(
            client.desktop_url("/api/group/savesetup"),
            data={
                "group_id": str(group_id),
                "group_setup": json.dumps(group_setup, ensure_ascii=False),
            },
        )
        if not isinstance(setup_resp, dict):
            return _err("SET_COURSE_AUTO_CLOSE_FAILED", "保存自动关闭提示文案失败")
        if setup_resp.get("status") not in (True, "true") and setup_resp.get("error_code") != 0:
            return _err(
                "SET_COURSE_AUTO_CLOSE_FAILED",
                setup_resp.get("error") or setup_resp.get("error_message") or "保存自动关闭提示文案失败",
            )

        return _ok(
            data={
                "group_id": str(group_id),
                "close_time": close_timestamp,
                "access_permission_tips": tips,
            },
            next_action="proceed",
            suggested_action="可通过 UMU 后台查看课程的自动关闭提示",
        )
    except Exception as e:
        logger.exception("设置课程自动关闭时间失败")
        return _err("SET_COURSE_AUTO_CLOSE_FAILED", str(e))


@mcp.tool()
async def tch_cancel_course_auto_close(
    group_id: Annotated[str, Field(description="课程 ID")],
    clear_tips: Annotated[
        bool,
        Field(
            default=True,
            description="是否同时清空自动关闭提示文案",
        ),
    ] = True,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """取消课程自动关闭时间.

    调用 /uapi/v1/course/set-timing-switch 将 close_time 置为 0，
    默认同时清空 accessPermissionTips 提示文案。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        resp = client.post(
            client.desktop_url("/uapi/v1/course/set-timing-switch"),
            data={
                "course_id": str(group_id),
                "open_time": "0",
                "close_time": "0",
            },
        )
        if not isinstance(resp, dict) or resp.get("error_code") != 0:
            return _err(
                "CANCEL_COURSE_AUTO_CLOSE_FAILED",
                resp.get("error_message") or "取消课程自动关闭时间失败",
            )

        if clear_tips:
            group_setup = {
                "accessPermissionTips": "",
                "access_permission_tips": "",
                "enable_mini_program": 0,
                "learn_within_mini_program": 0,
            }
            setup_resp = client.post(
                client.desktop_url("/api/group/savesetup"),
                data={
                    "group_id": str(group_id),
                    "group_setup": json.dumps(group_setup, ensure_ascii=False),
                },
            )
            if not isinstance(setup_resp, dict):
                return _err("CANCEL_COURSE_AUTO_CLOSE_FAILED", "清空自动关闭提示文案失败")
            if setup_resp.get("status") not in (True, "true") and setup_resp.get("error_code") != 0:
                return _err(
                    "CANCEL_COURSE_AUTO_CLOSE_FAILED",
                    setup_resp.get("error") or setup_resp.get("error_message") or "清空自动关闭提示文案失败",
                )

        return _ok(
            data={
                "group_id": str(group_id),
                "close_time": 0,
                "cleared_tips": clear_tips,
            },
            next_action="proceed",
        )
    except Exception as e:
        logger.exception("取消课程自动关闭时间失败")
        return _err("CANCEL_COURSE_AUTO_CLOSE_FAILED", str(e))


@mcp.tool()
async def tch_export_course_permissions(
    output_path: Annotated[
        str,
        Field(
            default="~/Desktop/umu_course_permissions.xlsx",
            description="输出文件路径，默认桌面。支持 .xlsx 或 .csv 扩展名。",
        ),
    ] = "~/Desktop/umu_course_permissions.xlsx",
    file_format: Annotated[
        str,
        Field(
            default="xlsx",
            pattern="^(xlsx|csv)$",
            description="文件格式：xlsx 或 csv。",
        ),
    ] = "xlsx",
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """导出当前讲师创建的所有课程的访问权限明细到 Excel/CSV.

    每门课程一行基础记录；对于指定账户可见（access_permission=3）的课程，
    会额外为每个授权对象生成一行，方便筛选和统计。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    # 处理路径中的 ~ 和环境变量
    output_path = os.path.expanduser(os.path.expandvars(output_path))

    # 根据 file_format 确保扩展名正确
    base, ext = os.path.splitext(output_path)
    if file_format == "csv":
        if ext.lower() != ".csv":
            output_path = f"{base}.csv"
    else:
        if ext.lower() != ".xlsx":
            output_path = f"{base}.xlsx"

    # 确保输出目录存在
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    try:
        engine = ExportEngine(client)
        result = engine.export_course_permissions(output_path)
        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="文件已生成，可直接在本地打开查看。",
        )
    except Exception as e:
        logger.exception("导出课程访问权限失败")
        return _err("EXPORT_COURSE_PERMISSIONS_FAILED", str(e))


@mcp.tool()
async def tch_export_program_permissions(
    output_path: Annotated[
        str,
        Field(
            default="~/Desktop/umu_program_permissions.xlsx",
            description="输出文件路径，默认桌面。支持 .xlsx 或 .csv 扩展名。",
        ),
    ] = "~/Desktop/umu_program_permissions.xlsx",
    file_format: Annotated[
        str,
        Field(
            default="xlsx",
            pattern="^(xlsx|csv)$",
            description="文件格式：xlsx 或 csv。",
        ),
    ] = "xlsx",
    scope: Annotated[
        str,
        Field(
            default="owned",
            pattern="^(owned|cooperated|enrolled)$",
            description="列表视角：owned=我拥有的, cooperated=协同给我的, enrolled=我报名的。",
        ),
    ] = "owned",
    keywords: Annotated[
        str | None,
        Field(
            default=None,
            description="按标题/访问码模糊搜索。",
        ),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """导出当前讲师的学习项目访问权限明细到 Excel/CSV.

    每个学习项目一行基础记录；对于指定账户可见（access_permission=3）的项目，
    会额外为每个授权对象生成一行，方便筛选和统计。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    # 处理路径中的 ~ 和环境变量
    output_path = os.path.expanduser(os.path.expandvars(output_path))

    # 根据 file_format 确保扩展名正确
    base, ext = os.path.splitext(output_path)
    if file_format == "csv":
        if ext.lower() != ".csv":
            output_path = f"{base}.csv"
    else:
        if ext.lower() != ".xlsx":
            output_path = f"{base}.xlsx"

    # 确保输出目录存在
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    try:
        engine = ExportEngine(client)
        result = engine.export_program_permissions(
            output_path,
            scope=scope,
            keywords=keywords,
        )
        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="文件已生成，可直接在本地打开查看。",
        )
    except Exception as e:
        logger.exception("导出学习项目访问权限失败")
        return _err("EXPORT_PROGRAM_PERMISSIONS_FAILED", str(e))


@mcp.tool()
async def tch_get_program_access_permission(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取学习项目当前的访问权限设置.

    返回当前学习项目的 access_permission（0=关闭，2=企业内公开，3=指定账户/班级/部门/分组），
    以及该学习项目可设置的所有权限选项。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        selected_int, options, detail = _get_obj_access_permission(client, program_id, "program")
        permission_text = _permission_text(selected_int) if selected_int >= 0 else "未知"

        return _ok(
            data={
                "program_id": str(program_id),
                "access_permission": selected_int,
                "permission_text": permission_text,
                "permission_options": options,
                "detail": detail,
            },
            next_action="proceed",
        )
    except Exception as e:
        logger.exception("获取学习项目访问权限失败")
        return _err("GET_PROGRAM_ACCESS_PERMISSION_FAILED", str(e))


@mcp.tool()
async def tch_set_program_access_permission(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    access_permission: Annotated[
        int,
        Field(description="权限：0=关闭，2=企业内公开，3=指定账户", ge=0, le=3),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """设置学习项目的整体访问权限.

    access_permission 取值：
      0 = 关闭（不可见）
      2 = 企业内公开
      3 = 指定账户/班级/部门/分组可见
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        detail = _set_obj_access_permission(
            client, program_id, "program", access_permission, update_session_permission=True
        )
        return _ok(
            data={
                "program_id": str(program_id),
                "access_permission": access_permission,
                "permission_text": _permission_text(access_permission),
                "detail": detail,
            },
            next_action="proceed",
            suggested_action=(
                "access_permission=3 时，请继续调用 tch_add_program_access_accounts 添加可见对象"
                if access_permission == 3 else ""
            ),
        )
    except Exception as e:
        logger.exception("设置学习项目访问权限失败")
        return _err("SET_PROGRAM_ACCESS_PERMISSION_FAILED", str(e))


@mcp.tool()
async def tch_get_program_access_list(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    page: Annotated[int, Field(default=1, description="页码", ge=1)] = 1,
    size: Annotated[int, Field(default=20, description="每页数量", ge=1, le=100)] = 20,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取学习项目当前已授权的访问列表.

    当学习项目 access_permission 为 3 时，返回已授权的账户/班级/部门/分组列表。
    返回结果中的 account_type 字段标识对象类型：user/class/department/group。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        items, page_info = _get_obj_access_list(client, program_id, "program", page, size)
        return _ok(
            data={
                "program_id": str(program_id),
                "page": page,
                "size": size,
                "page_info": page_info,
                "list": items,
                "total": page_info.get("list_total_num", len(items)),
            },
            next_action="proceed",
        )
    except Exception as e:
        logger.exception("获取学习项目访问列表失败")
        return _err("GET_PROGRAM_ACCESS_LIST_FAILED", str(e))


@mcp.tool()
async def tch_search_program_access_accounts(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    keyword: Annotated[str, Field(description="搜索关键词：账户邮箱、姓名、班级名称、部门名称或分组名称，支持模糊匹配")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """搜索可授权访问学习项目的账户、班级、部门或分组.

    返回结果中 account_type 为 user 表示账户，class 表示班级，
    department 表示部门，group 表示分组。
    建议优先使用完整邮箱搜索账户，以提高匹配准确度。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        ok, accounts, err = _search_access_permission_account(client, program_id, "program", keyword)
        if not ok:
            return _err("SEARCH_PROGRAM_ACCESS_ACCOUNTS_FAILED", err or "搜索可授权账户失败")

        return _ok(
            data={
                "program_id": str(program_id),
                "keyword": keyword,
                "accounts": [_format_access_account(acc) for acc in accounts],
                "total": len(accounts),
            },
            next_action="proceed",
            suggested_action="请从结果中选择目标账户/班级，调用 tch_add_program_access_accounts 添加权限",
        )
    except Exception as e:
        logger.exception("搜索学习项目可授权账户失败")
        return _err("SEARCH_PROGRAM_ACCESS_ACCOUNTS_FAILED", str(e))


@mcp.tool()
async def tch_add_program_access_accounts(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    accounts: Annotated[
        list[dict[str, Any]],
        Field(
            description="要添加的账户/班级/部门/分组列表，每个元素需包含 account、account_type(user/class/department/group)、id，"
                        "class 类型还需包含 class_id，department 类型还需包含 department_id，"
                        "group 类型还需包含 user_group_id（可调用 tch_search_program_access_accounts 获取）",
        ),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """为学习项目添加指定账户/班级/部门/分组可见权限.

    前置条件：学习项目 access_permission 需为 3（指定账户可见）。
    如当前不是该状态，工具会先自动调用 setprogrampermission 设置为 3，再添加对象。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    if not accounts:
        return _err(
            "NO_ACCOUNTS_PROVIDED",
            "未提供要添加的账户/班级",
            suggested_action="请先调用 tch_search_program_access_accounts 查询目标",
            next_action="needs_user_input",
        )

    try:
        detail = _add_obj_access_accounts(client, program_id, "program", accounts, update_session_permission=True)
        return _ok(
            data={
                "program_id": str(program_id),
                "added": len(accounts),
                "accounts": accounts,
                "detail": detail,
            },
            next_action="proceed",
            suggested_action="可调用 tch_search_program_access_accounts 或查看 UMU 后台确认权限已生效",
        )
    except Exception as e:
        logger.exception("添加学习项目指定账户失败")
        return _err("ADD_PROGRAM_ACCESS_ACCOUNTS_FAILED", str(e))


@mcp.tool()
async def tch_remove_program_access_accounts(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    accounts: Annotated[
        list[dict[str, Any]],
        Field(
            description="要移除的账户/班级/部门/分组列表，每个元素需包含 account、account_type(user/class/department/group)、id，"
                        "class 类型还需包含 class_id，department 类型还需包含 department_id，"
                        "group 类型还需包含 user_group_id",
        ),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """移除学习项目的指定账户/班级/部门/分组访问权限."""
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    if not accounts:
        return _err(
            "NO_ACCOUNTS_PROVIDED",
            "未提供要移除的账户/班级",
            next_action="needs_user_input",
        )

    try:
        detail = _remove_obj_access_accounts(client, program_id, "program", accounts, update_session_permission=True)
        return _ok(
            data={
                "program_id": str(program_id),
                "removed": len(accounts),
                "accounts": accounts,
                "detail": detail,
            },
            next_action="proceed",
        )
    except Exception as e:
        logger.exception("移除学习项目指定账户失败")
        return _err("REMOVE_PROGRAM_ACCESS_ACCOUNTS_FAILED", str(e))


@mcp.tool()
async def tch_cancel_all_program_permissions(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """取消学习项目的所有指定访问权限.

    调用后会清空学习项目的指定账户/班级列表，常用于将学习项目还原为企业内公开前的清理。
    如需同时设置为企业内公开，请在调用后继续调用 tch_set_program_access_permission。
    """
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    try:
        detail = _cancel_all_assigned_permissions(client, program_id, "program")
        return _ok(
            data={
                "program_id": str(program_id),
                "detail": detail,
            },
            next_action="proceed",
            suggested_action="如需还原为企业内公开，请调用 tch_set_program_access_permission(program_id, access_permission=2)",
        )
    except Exception as e:
        logger.exception("取消学习项目指定权限失败")
        return _err("CANCEL_PROGRAM_ACCESS_PERMISSIONS_FAILED", str(e))


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    """MCP 服务入口."""
    import asyncio

    print("=" * 60)
    print("UMU 讲师端 MCP Server")
    print("=" * 60)
    print()
    print("支持的传输方式:")
    print("  - stdio:  标准输入输出（推荐用于本地 AI 助手）")
    print()
    print("环境变量:")
    print("  UMU_BASE_URL       - UMU 基础 URL（默认: https://www.umu.cn）")
    print("  UMU_TEACHER_USERNAME - 讲师登录用户名")
    print("  UMU_TEACHER_PASSWORD - 讲师登录密码")
    print("  MCP_LOG_LEVEL      - 日志级别 (DEBUG|INFO|WARNING|ERROR，默认: INFO)")
    print()
    print("可用 Tools:")
    print("  认证: tch_login, tch_check_auth")
    print("  会话: tch_create_session, tch_list_sessions, tch_destroy_session")
    print("  课程: tch_create_course, tch_get_categories,")
    print("        tch_list_created_courses, tch_list_cooperated_courses,")
    print("        tch_list_participated_courses,")
    print("        tch_get_course, tch_get_course_detail（含小节列表+资源删除检测）,")
    print("        tch_update_course（综合修改）,")
    print("        tch_update_course_basic, tch_update_course_type,")
    print("        tch_update_course_category, tch_update_course_schedule,")
    print("        tch_update_course_images, tch_update_course_richtext,")
    print("  课程权限: tch_set_course_access_permission, tch_get_course_access_permission,")
    print("            tch_get_course_access_list, tch_search_access_accounts,")
    print("            tch_add_course_access_accounts, tch_remove_course_access_accounts,")
    print("            tch_cancel_all_assigned_permissions,")
    print("  学习项目权限: tch_set_program_access_permission, tch_get_program_access_permission,")
    print("                tch_get_program_access_list, tch_search_program_access_accounts,")
    print("                tch_add_program_access_accounts, tch_remove_program_access_accounts,")
    print("                tch_cancel_all_program_permissions,")
    print("  小节: tch_create_scorm_section, tch_create_document_section,")
    print("        tch_create_video_section, tch_create_article_section,")
    print("        tch_create_infographic_section, tch_create_survey_section,")
    print("        tch_create_exam_section,")
    print("        tch_update_exam_section,")
    print("        tch_list_sections, tch_get_section,")
    print("        tch_update_scorm_section, tch_update_document_section,")
    print("        tch_update_article_section, tch_update_infographic_section,")
    print("        tch_update_survey_section,")
    print("        tch_get_infographic_content,")
    print("        tch_toggle_section_visibility, tch_delete_section")
    print("  SCORM资源: tch_upload_scorm, tch_list_resources, tch_rename_resource, tch_delete_resource")
    print("  文档管理: tch_upload_document, tch_list_documents, tch_rename_document, tch_delete_document")
    print("  批量操作: tch_upload_documents_batch, tch_delete_documents_batch")
    print("  音视频管理: tch_upload_audio_video, tch_list_audio_videos, tch_rename_audio_video, tch_delete_audio_video")
    print()

    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport == "stdio":
        asyncio.run(mcp.run_stdio_async())
    elif transport == "sse":
        asyncio.run(mcp.run_sse_async())
    else:
        print(f"不支持的传输方式: {transport}")
        print("支持: stdio, sse")


if __name__ == "__main__":
    main()
