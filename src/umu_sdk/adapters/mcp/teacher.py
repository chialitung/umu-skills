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
    format_login_summary,
    get_login_identity,
)
from .cos_upload import (
    ScormUploader,
    validate_file_path,
)
from .course_builder import CourseBuilder
from .document_upload import (
    DocumentUploader,
    validate_document_path,
)
from .export_engine import ExportEngine
from .image_upload import ImageUploader
from .session import SessionManager
from .shared_access_permissions import (
    _parse_access_permission_response as _parse_collaboration_response,
)
from .shared_session_tools import (
    SessionToolConfig,
    make_check_auth_tool,
    make_create_session_tool,
    make_destroy_session_tool,
    make_list_sessions_tool,
    make_login_tool,
)
from .tool_factory import register_operations
from ...tools.operations import programs as _programs_ops
from ...tools.operations import access_permissions as _access_permissions_ops
from ...tools.operations import courses as _courses_ops
from ...tools.operations import learning as _learning_ops
from ...tools.operations import course_management as _course_management_ops
from ...tools.operations import section_management as _section_management_ops
from ...tools.operations import resource_management as _resource_management_ops
from ...tools.operations import collaboration as _collaboration_ops

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


def _get_client_for_ops(session_id: str | None = None) -> UMUClient:
    """运行时分发 client；通过包装层保留测试对 _get_client 的 patch 能力."""
    return _get_client(session_id)


def _require_auth_for_ops(client: UMUClient) -> str | None:
    """运行时分发鉴权检查；通过包装层保留测试对 _require_auth 的 patch 能力."""
    return _require_auth(client)


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
# Tools: 共享业务操作（自动注册）
# ---------------------------------------------------------------------------

register_operations(
    mcp=mcp,
    module=_programs_ops,
    role="teacher",
    get_client=_get_client_for_ops,
    ok=_ok,
    err=_err,
    require_auth=_require_auth_for_ops,
    logger=logger,
    namespace=globals(),
)

register_operations(
    mcp=mcp,
    module=_access_permissions_ops,
    role="teacher",
    get_client=_get_client_for_ops,
    ok=_ok,
    err=_err,
    require_auth=_require_auth_for_ops,
    logger=logger,
    namespace=globals(),
)

register_operations(
    mcp=mcp,
    module=_courses_ops,
    role="teacher",
    get_client=_get_client_for_ops,
    ok=_ok,
    err=_err,
    require_auth=_require_auth_for_ops,
    logger=logger,
    namespace=globals(),
)

register_operations(
    mcp=mcp,
    module=_learning_ops,
    role="teacher",
    get_client=_get_client_for_ops,
    ok=_ok,
    err=_err,
    require_auth=_require_auth_for_ops,
    logger=logger,
    namespace=globals(),
)

register_operations(
    mcp=mcp,
    module=_course_management_ops,
    role="teacher",
    get_client=_get_client_for_ops,
    ok=_ok,
    err=_err,
    require_auth=_require_auth_for_ops,
    logger=logger,
    namespace=globals(),
)

register_operations(
    mcp=mcp,
    module=_section_management_ops,
    role="teacher",
    get_client=_get_client_for_ops,
    ok=_ok,
    err=_err,
    require_auth=_require_auth_for_ops,
    logger=logger,
    namespace=globals(),
)

register_operations(
    mcp=mcp,
    module=_resource_management_ops,
    role="teacher",
    get_client=_get_client_for_ops,
    ok=_ok,
    err=_err,
    require_auth=_require_auth_for_ops,
    logger=logger,
    namespace=globals(),
)

register_operations(
    mcp=mcp,
    module=_collaboration_ops,
    role="teacher",
    get_client=_get_client_for_ops,
    ok=_ok,
    err=_err,
    require_auth=_require_auth_for_ops,
    logger=logger,
    namespace=globals(),
)


# ---------------------------------------------------------------------------
# Tools: 资源管理
# ---------------------------------------------------------------------------







# ---------------------------------------------------------------------------
# Tools: 文档管理（我的文档）
# ---------------------------------------------------------------------------











# ---------------------------------------------------------------------------
# Tools: 音视频管理（我的音视频）
# ---------------------------------------------------------------------------







# ---------------------------------------------------------------------------
# Tools: 课程管理
# ---------------------------------------------------------------------------







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






# ---------------------------------------------------------------------------
# Tools: 课程信息获取与修改
# ---------------------------------------------------------------------------







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


# ---------------------------------------------------------------------------
# Tools: 课程列表查询（我的课程）
# ---------------------------------------------------------------------------






















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








# ---------------------------------------------------------------------------
# 课程自动关闭
# ---------------------------------------------------------------------------








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