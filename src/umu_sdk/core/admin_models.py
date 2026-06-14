"""Admin MCP 账号管理相关 Pydantic 模型.

本模块将账号清单接口的数据结构代码化，供后续数据处理、类型检查
和序列化使用。字段说明详见 docs/admin/account-data-dictionary.md。
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


_BEIJING_TZ = timezone(timedelta(hours=8))


def format_timestamp_beijing(ts: int) -> str:
    """将 Unix 时间戳转换为北京时间字符串."""
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=_BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


class AdminAccountStatus:
    """账号状态码.

    注意：不同企业的 UMU 平台状态码映射可能存在差异，
    请以 adm_list_accounts 的 account_status 筛选结果为准确认实际映射。
    """

    PENDING = 0
    ENABLED = 1
    SCHEDULED_DISABLED = 2
    DISABLED = 3


class AdminRoleType:
    """角色类型码."""

    STUDENT = 1
    TEACHER = 2
    LEARNING_MANAGER = 3
    SYSTEM_ADMIN = 4
    SUB_ADMIN = 5


_ROLE_TYPE_MAP = {
    AdminRoleType.STUDENT: "学员",
    AdminRoleType.TEACHER: "讲师",
    AdminRoleType.LEARNING_MANAGER: "学习负责人",
    AdminRoleType.SYSTEM_ADMIN: "系统管理员",
    AdminRoleType.SUB_ADMIN: "子管理员",
}

_STATUS_TEXT_MAP = {
    AdminAccountStatus.PENDING: "待加入",
    AdminAccountStatus.ENABLED: "已启用",
    AdminAccountStatus.SCHEDULED_DISABLED: "定时禁用",
    AdminAccountStatus.DISABLED: "已禁用",
}


def get_role_name(role_type: int) -> str:
    """将角色类型码转换为人读文本."""
    return _ROLE_TYPE_MAP.get(role_type, f"未知角色({role_type})")


def get_status_text(status_code: int) -> str:
    """将账号状态码转换为人读文本."""
    return _STATUS_TEXT_MAP.get(status_code, f"未知状态({status_code})")


class AdminAccountRaw(BaseModel):
    """UMU 原始账号对象.

    对应 /ajax/enterprise/getUserList 响应中 `data.list[]` 的单个元素。
    字段名和类型均保持原始接口返回形态。
    """

    model_config = ConfigDict(populate_by_name=True)

    umu_id: str
    is_active: Literal["0", "1"] = "1"
    role_type: str
    status: str
    number: str = ""
    platform_permission: str = "1"
    user_name: str
    user_name_letter: str = ""
    area_code: str = ""
    phone: str = ""
    email: str = ""
    login_name: str = ""
    account_joining_time: int = 0
    first_login_time: int = 0
    last_login_time: int = 0
    invite_url: str = ""
    account_status: int = 0
    effective_time: int = 0
    departments: str = "-"


class AdminAccount(BaseModel):
    """Admin MCP 标准化账号对象.

    对应 adm_list_accounts 返回的 `data.accounts[]` 单个元素。
    在原始字段基础上做了类型转换和补充计算字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    umu_id: str = Field(..., description="UMU 用户唯一标识")
    user_name: str = Field(..., description="用户姓名/企业名称")
    email: str = Field(default="", description="邮箱地址")
    phone: str = Field(default="", description="手机号")
    login_name: str = Field(default="", description="登录用户名")
    number: str = Field(default="", description="员工编号/工号")
    account_status: int = Field(..., description="账号状态码（数值）")
    status_text: str = Field(..., description="状态人读文本")
    is_active: str = Field(default="1", description='是否活跃，"1"=活跃，"0"=不活跃')
    role_type: int = Field(..., description="角色类型码")
    role_name: str = Field(..., description="角色人读文本")
    departments: str = Field(default="-", description="所属部门，多个部门可能以逗号分隔")
    account_joining_time: int = Field(default=0, description="账号加入时间，Unix 时间戳（秒）")
    account_joining_time_readable: str = Field(
        default="", description="账号加入时间，北京时间字符串"
    )
    first_login_time: int = Field(default=0, description="首次登录时间，Unix 时间戳（秒）")
    first_login_time_readable: str = Field(default="", description="首次登录时间，北京时间字符串")
    last_login_time: int = Field(default=0, description="最后登录时间，Unix 时间戳（秒）")
    last_login_time_readable: str = Field(default="", description="最后登录时间，北京时间字符串")

    @classmethod
    def from_raw(cls, raw: AdminAccountRaw) -> "AdminAccount":
        """从原始 UMU 账号对象构造标准化对象."""
        status_code = int(raw.account_status or 0)
        role_code = int(raw.role_type or 0)
        return cls(
            umu_id=raw.umu_id,
            user_name=raw.user_name,
            email=raw.email,
            phone=raw.phone,
            login_name=raw.login_name,
            number=raw.number,
            account_status=status_code,
            status_text=get_status_text(status_code),
            is_active=raw.is_active,
            role_type=role_code,
            role_name=get_role_name(role_code),
            departments=raw.departments,
            account_joining_time=raw.account_joining_time,
            account_joining_time_readable=format_timestamp_beijing(raw.account_joining_time),
            first_login_time=raw.first_login_time,
            first_login_time_readable=format_timestamp_beijing(raw.first_login_time),
            last_login_time=raw.last_login_time,
            last_login_time_readable=format_timestamp_beijing(raw.last_login_time),
        )


class AdminAccountListPageInfo(BaseModel):
    """UMU 原始分页信息."""

    model_config = ConfigDict(populate_by_name=True)

    list_total_num: int = Field(..., description="符合条件的账号总数")
    total_page_num: int = Field(..., description="总页数")
    current_page: int = Field(..., description="当前页码")
    size: int = Field(..., description="当前页大小")


class AdminAccountListPagination(BaseModel):
    """MCP 标准化分页信息."""

    model_config = ConfigDict(populate_by_name=True)

    total_all: int = Field(..., description="符合条件的账号总数")
    current_page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="当前页大小")


class AdminAccountListData(BaseModel):
    """MCP 标准化账号列表数据."""

    model_config = ConfigDict(populate_by_name=True)

    accounts: list[AdminAccount] = Field(..., description="账号列表")
    total: int = Field(..., description="本次返回账号数量")
    pagination: AdminAccountListPagination = Field(..., description="分页信息")


class AdminAccountListResponse(BaseModel):
    """MCP 标准化账号列表响应."""

    model_config = ConfigDict(populate_by_name=True)

    success: bool
    data: AdminAccountListData
    error_code: str = ""
    error_message: str = ""
    suggested_action: str = ""
    next_action: Literal["proceed", "needs_enrollment", "needs_user_input", "lesson_completed"] = (
        "proceed"
    )


class AdminAccountListParams(BaseModel):
    """账号列表查询参数."""

    model_config = ConfigDict(populate_by_name=True)

    is_manager: Literal["0", "1"] = "0"
    page: str = "1"
    size: str = "500"
    group_operator: Literal["intersection", "union"] = "intersection"
    keywords: str | None = None
    group_ids: str | None = None
    role_type: str | None = None
    account_status: str | None = None


class LearningRecordRaw(BaseModel):
    """UMU 原始课程学习记录对象.

    对应 /uapi/v1/dashboard/learning-group-list 响应中 `data.list[]` 的单个元素。
    字段名和类型均保持原始接口返回形态。
    """

    model_config = ConfigDict(populate_by_name=True)

    first_learning_time: str = "0"
    last_learning_time: str = "0"
    sum_learning_time: str = ""
    group_required_session_total_count: int = 0
    group_required_session_finished_count: int = 0
    group_completion_rate: float = 0.0
    group_overall_completion_rate: float = 0.0
    group_completion_time: str | None = None
    group_overall_completion_time: int | None = None
    group_total_points: int = 0
    group_total_points_rank: int = 0
    id: str = ""
    enterprise_id: str = ""
    create_time: str = ""
    update_time: str = ""
    user_enterprise_id: str = ""
    umu_id: str = ""
    student_id: str = ""
    teacher_id: str = ""
    has_actived: str = "0"
    user_type: str = ""
    register_from: str = ""
    user_name: str = ""
    email: str = ""
    number: str = ""
    on_job_status: int = 0
    phone: str = ""
    login_name: str = ""
    avatar: str = ""
    enterprise_groups: list[str] = Field(default_factory=list)
    enterprise_departments: list[str] = Field(default_factory=list)
    class_names: list[str] = Field(default_factory=list, alias="class")
    group_id: str = ""
    group_title: str = ""
    group_share_url: str = ""
    group_access_code: str = ""
    is_assigned_task: bool = False
    vlt: str = ""


class LearningRecord(BaseModel):
    """Admin MCP 标准化课程学习记录对象.

    对应 adm_list_learning_records 返回的 `data.records[]` 单个元素。
    在原始字段基础上做了类型转换和补充计算字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    umu_id: str = Field(default="", description="UMU 用户唯一标识")
    student_id: str = Field(default="", description="学员 ID")
    teacher_id: str = Field(default="", description="讲师 ID")
    user_name: str = Field(default="", description="用户姓名")
    email: str = Field(default="", description="邮箱地址")
    phone: str = Field(default="", description="手机号")
    login_name: str = Field(default="", description="登录用户名")
    number: str = Field(default="", description="员工编号/工号")
    enterprise_groups: list[str] = Field(default_factory=list, description="所属企业分组")
    enterprise_departments: list[str] = Field(default_factory=list, description="所属部门")
    class_names: list[str] = Field(default_factory=list, description="所属班级")
    group_id: str = Field(default="", description="课程分组 ID")
    group_title: str = Field(default="", description="课程标题")
    group_share_url: str = Field(default="", description="课程分享链接")
    group_access_code: str = Field(default="", description="课程访问码")
    is_assigned_task: bool = Field(default=False, description="是否为指派任务")
    first_learning_time: int = Field(default=0, description="首次学习时间，Unix 时间戳（秒）")
    first_learning_time_readable: str = Field(
        default="", description="首次学习时间，北京时间字符串"
    )
    last_learning_time: int = Field(default=0, description="最后学习时间，Unix 时间戳（秒）")
    last_learning_time_readable: str = Field(default="", description="最后学习时间，北京时间字符串")
    sum_learning_time: str = Field(default="", description="累计学习时长")
    group_required_session_total_count: int = Field(default=0, description="课程必修小节总数")
    group_required_session_finished_count: int = Field(default=0, description="已完成必修小节数")
    group_completion_rate: float = Field(default=0.0, description="课程完成率（0-1）")
    group_overall_completion_rate: float = Field(default=0.0, description="整体完成率（0-1）")
    group_completion_time: int = Field(default=0, description="课程完成时间，Unix 时间戳（秒）")
    group_completion_time_readable: str = Field(
        default="", description="课程完成时间，北京时间字符串"
    )
    group_overall_completion_time: int = Field(
        default=0, description="整体完成时间，Unix 时间戳（秒）"
    )
    group_overall_completion_time_readable: str = Field(
        default="", description="整体完成时间，北京时间字符串"
    )
    group_total_points: int = Field(default=0, description="课程总得分")
    group_total_points_rank: int = Field(default=0, description="得分排名")
    vlt: str = Field(default="", description="视频学习时长")

    @classmethod
    def from_raw(cls, raw: LearningRecordRaw) -> "LearningRecord":
        """从原始 UMU 学习记录对象构造标准化对象."""
        first_ts = int(raw.first_learning_time or 0)
        last_ts = int(raw.last_learning_time or 0)
        completion_ts = int(raw.group_completion_time or 0)
        overall_completion_ts = raw.group_overall_completion_time or 0

        return cls(
            umu_id=raw.umu_id,
            student_id=raw.student_id,
            teacher_id=raw.teacher_id,
            user_name=raw.user_name,
            email=raw.email,
            phone=raw.phone,
            login_name=raw.login_name,
            number=raw.number,
            enterprise_groups=raw.enterprise_groups,
            enterprise_departments=raw.enterprise_departments,
            class_names=raw.class_names,
            group_id=raw.group_id,
            group_title=raw.group_title,
            group_share_url=raw.group_share_url,
            group_access_code=raw.group_access_code,
            is_assigned_task=raw.is_assigned_task,
            first_learning_time=first_ts,
            first_learning_time_readable=format_timestamp_beijing(first_ts),
            last_learning_time=last_ts,
            last_learning_time_readable=format_timestamp_beijing(last_ts),
            sum_learning_time=raw.sum_learning_time,
            group_required_session_total_count=raw.group_required_session_total_count,
            group_required_session_finished_count=raw.group_required_session_finished_count,
            group_completion_rate=float(raw.group_completion_rate or 0),
            group_overall_completion_rate=float(raw.group_overall_completion_rate or 0),
            group_completion_time=completion_ts,
            group_completion_time_readable=format_timestamp_beijing(completion_ts),
            group_overall_completion_time=overall_completion_ts,
            group_overall_completion_time_readable=format_timestamp_beijing(overall_completion_ts),
            group_total_points=raw.group_total_points,
            group_total_points_rank=raw.group_total_points_rank,
            vlt=raw.vlt,
        )


class LearningRecordListPageInfo(BaseModel):
    """UMU 原始学习记录分页信息."""

    model_config = ConfigDict(populate_by_name=True)

    list_total_num: int = Field(..., description="符合条件的记录总数")
    total_page_num: int = Field(..., description="总页数")
    current_page: int = Field(..., description="当前页码")
    size: int = Field(..., description="当前页大小")


class LearningRecordListPagination(BaseModel):
    """MCP 标准化学习记录分页信息."""

    model_config = ConfigDict(populate_by_name=True)

    total_all: int = Field(..., description="符合条件的记录总数")
    current_page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="当前页大小")


class LearningRecordListData(BaseModel):
    """MCP 标准化学习记录列表数据."""

    model_config = ConfigDict(populate_by_name=True)

    records: list[LearningRecord] = Field(..., description="学习记录列表")
    total: int = Field(..., description="本次返回记录数量")
    pagination: LearningRecordListPagination = Field(..., description="分页信息")


class LearningRecordListResponse(BaseModel):
    """MCP 标准化学习记录列表响应."""

    model_config = ConfigDict(populate_by_name=True)

    success: bool
    data: LearningRecordListData
    error_code: str = ""
    error_message: str = ""
    suggested_action: str = ""
    next_action: Literal["proceed", "needs_enrollment", "needs_user_input", "lesson_completed"] = (
        "proceed"
    )


class AdminClassRaw(BaseModel):
    """UMU 原始班级对象.

    对应 /uapi/v1/enterprise/class-list 响应中 `data.list[]` 的单个元素。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    access_code: str = ""
    create_teacher_id: str = ""
    cover_image: str = ""


class AdminClass(BaseModel):
    """Admin MCP 标准化班级对象."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., description="班级 ID")
    name: str = Field(..., description="班级名称")
    access_code: str = Field(default="", description="班级访问码")
    create_teacher_id: str = Field(default="", description="创建者教师 ID")
    cover_image: str = Field(default="", description="班级封面图 URL")

    @classmethod
    def from_raw(cls, raw: AdminClassRaw) -> "AdminClass":
        """从原始 UMU 班级对象构造标准化对象."""
        return cls(
            id=raw.id,
            name=raw.name,
            access_code=raw.access_code,
            create_teacher_id=raw.create_teacher_id,
            cover_image=raw.cover_image,
        )


class AdminClassListPageInfo(BaseModel):
    """UMU 原始班级分页信息."""

    model_config = ConfigDict(populate_by_name=True)

    list_total_num: int = Field(..., description="符合条件的班级总数")
    total_page_num: int = Field(..., description="总页数")
    current_page: int = Field(..., description="当前页码")
    size: int = Field(..., description="当前页大小")


class AdminClassListPagination(BaseModel):
    """MCP 标准化班级分页信息."""

    model_config = ConfigDict(populate_by_name=True)

    total_all: int = Field(..., description="符合条件的班级总数")
    current_page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="当前页大小")


class AdminClassListData(BaseModel):
    """MCP 标准化班级列表数据."""

    model_config = ConfigDict(populate_by_name=True)

    classes: list[AdminClass] = Field(..., description="班级列表")
    total: int = Field(..., description="本次返回班级数量")
    pagination: AdminClassListPagination = Field(..., description="分页信息")


class AdminClassListResponse(BaseModel):
    """MCP 标准化班级列表响应."""

    model_config = ConfigDict(populate_by_name=True)

    success: bool
    data: AdminClassListData
    error_code: str = ""
    error_message: str = ""
    suggested_action: str = ""
    next_action: Literal["proceed", "needs_enrollment", "needs_user_input", "lesson_completed"] = (
        "proceed"
    )


# ---------------------------------------------------------------------------
# 课程清单
# ---------------------------------------------------------------------------


class AdminCourseAuditStatus:
    """课程审核状态码.

    对应 getReportGroupList 响应中的 audit_status 字段。
    """

    UNSUBMITTED = -1
    PENDING = 0
    APPROVED = 1
    REJECTED = 2
    CANCELLED = 3


class AdminCourseAccessPermission:
    """课程权限（可见范围）码.

    对应 getReportGroupList 请求/响应中的 access_permission 字段。
    """

    CLOSED = 0
    PUBLIC = 1
    ENTERPRISE_PUBLIC = 2
    ASSIGNED_ACCOUNTS = 3


class AdminCourseSource:
    """课程来源."""

    INNER = "inner"
    OUTER = "outer"


_AUDIT_STATUS_TEXT_MAP = {
    AdminCourseAuditStatus.UNSUBMITTED: "未提交",
    AdminCourseAuditStatus.PENDING: "待审核",
    AdminCourseAuditStatus.APPROVED: "已通过",
    AdminCourseAuditStatus.REJECTED: "已拒绝",
    AdminCourseAuditStatus.CANCELLED: "已撤销",
}


_ACCESS_PERMISSION_TEXT_MAP = {
    AdminCourseAccessPermission.CLOSED: "关闭",
    AdminCourseAccessPermission.PUBLIC: "公开",
    AdminCourseAccessPermission.ENTERPRISE_PUBLIC: "企业内公开",
    AdminCourseAccessPermission.ASSIGNED_ACCOUNTS: "指定账户",
}


def get_course_audit_status_text(status_code: int) -> str:
    """将课程审核状态码转换为人读文本."""
    return _AUDIT_STATUS_TEXT_MAP.get(status_code, f"未知状态({status_code})")


def get_course_access_permission_text(permission_code: int) -> str:
    """将课程权限码转换为人读文本."""
    return _ACCESS_PERMISSION_TEXT_MAP.get(permission_code, f"未知权限({permission_code})")


class AdminCourseRaw(BaseModel):
    """UMU 原始课程对象.

    对应 /ajax/enterprise/getReportGroupList 响应中 `data.list[]` 的单个元素。
    字段名和类型均保持原始接口返回形态。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = ""
    teacher_id: str = ""
    enterprise_id: int = 0
    type: str = "1"
    stime: str = "0"
    etime: str = "0"
    title: str = ""
    remark: str = ""
    province: str = ""
    isimportant: str = "0"
    eventType: str = "7"
    courseType: str = "1"
    customerName: str = ""
    coursePerson: str = "0"
    contactPhone: str = ""
    city: str = ""
    town: str = ""
    address: str = ""
    contact: str = ""
    max_online_user: str = "0"
    max_user_count: str = "0"
    group_in_use: str = "0"
    search_text: str = ""
    creat_time: str = "0"
    source: str = "inner"
    desc: str = ""
    setup: str = ""
    permission: str = "0"
    update_time: str = "0"
    head_img: str = ""
    bg_img: str = ""
    im_rid: str = "0"
    lesson_type: str = "0"
    other_lesson_type: str = ""
    content_type: str = "0"
    other_content_type: str = ""
    access_permission: str = "2"
    multimedia_id: str = "0"
    multimedia_type: str = "0"
    is_lock: str = "0"
    parent_obj_id: str = "0"
    is_in_trust: str = "0"
    is_repetitive_mode: str = "0"
    repetitive_course_lock: str = "0"
    audit_status: int = -1
    is_course_in_lib: int = 0
    group_time: list[Any] = Field(default_factory=list)
    username: str = ""
    avatar: str = ""
    student_id: str = ""
    umu_id: str = ""
    partticipate_num: int = 0
    weike_star_avg: int = 0
    like_num: int = 0
    finish_num: int = 0
    session_count: int = 0
    session_num: int = 0
    weike_time: str = "0"
    vlt: str = "0"
    learning_time: str = "0"
    lecturing_teacher: list[Any] = Field(default_factory=list)
    assignment_count: int = 0
    share_url: str = ""
    tags: list[str] = Field(default_factory=list)
    categoryArr: list[Any] = Field(default_factory=list)
    enterprise_groups: list[Any] = Field(default_factory=list)
    enterprise_departments: list[Any] = Field(default_factory=list)
    access_code: str = ""
    u_course_score: int = 0
    has_group_report: int = 0
    first_learning_ts: str = "0"
    last_learning_ts: str = "0"
    sum_learning_time: str = "0"
    group_id: str = ""


class AdminCourse(BaseModel):
    """Admin MCP 标准化课程对象.

    对应 adm_list_courses 返回的 `data.courses[]` 单个元素。
    在原始字段基础上做了类型转换和补充计算字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    group_id: str = Field(default="", description="课程分组 ID（同 id / group_id）")
    title: str = Field(default="", description="课程标题")
    access_code: str = Field(default="", description="课程访问码")
    share_url: str = Field(default="", description="课程分享链接")
    teacher_id: str = Field(default="", description="创建者教师 ID")
    creator_umu_id: str = Field(default="", description="创建者 UMU 用户 ID")
    creator_username: str = Field(default="", description="创建者用户名")
    head_img: str = Field(default="", description="课程封面图 URL")
    bg_img: str = Field(default="", description="课程背景图 URL")
    start_time: int = Field(default=0, description="课程开始时间，Unix 时间戳（秒）")
    end_time: int = Field(default=0, description="课程结束时间，Unix 时间戳（秒）")
    create_time: int = Field(default=0, description="课程创建时间，Unix 时间戳（秒）")
    update_time: int = Field(default=0, description="课程最后更新时间，Unix 时间戳（秒）")
    start_time_readable: str = Field(default="", description="课程开始时间，北京时间字符串")
    end_time_readable: str = Field(default="", description="课程结束时间，北京时间字符串")
    create_time_readable: str = Field(default="", description="课程创建时间，北京时间字符串")
    update_time_readable: str = Field(default="", description="课程最后更新时间，北京时间字符串")
    session_count: int = Field(default=0, description="小节数量")
    participant_num: int = Field(default=0, description="参与人数")
    finish_num: int = Field(default=0, description="完成人数")
    learning_time: str = Field(default="", description="学习时长展示文本")
    tags: list[str] = Field(default_factory=list, description="课程标签列表")
    audit_status: int = Field(default=-1, description="审核状态码")
    audit_status_text: str = Field(default="", description="审核状态人读文本")
    is_course_in_lib: int = Field(default=0, description="是否在企业知识库/课程库中，0=否，1=是")
    access_permission: int = Field(default=2, description="课程权限码")
    access_permission_text: str = Field(default="", description="课程权限人读文本")
    source: str = Field(default="inner", description="课程来源，inner=内部，outer=外部")
    has_group_report: int = Field(default=0, description="是否有课程报告，0=否，1=是")
    u_course_score: int = Field(default=0, description="课程学分/积分")
    first_learning_time: int = Field(default=0, description="首次学习时间，Unix 时间戳（秒）")
    last_learning_time: int = Field(default=0, description="最后学习时间，Unix 时间戳（秒）")
    first_learning_time_readable: str = Field(
        default="", description="首次学习时间，北京时间字符串"
    )
    last_learning_time_readable: str = Field(default="", description="最后学习时间，北京时间字符串")

    @classmethod
    def from_raw(cls, raw: AdminCourseRaw) -> "AdminCourse":
        """从原始 UMU 课程对象构造标准化对象."""
        start_ts = int(raw.stime or 0)
        end_ts = int(raw.etime or 0)
        create_ts = int(raw.creat_time or 0)
        update_ts = int(raw.update_time or 0)
        first_ts = int(raw.first_learning_ts or 0)
        last_ts = int(raw.last_learning_ts or 0)

        audit_status = int(raw.audit_status if raw.audit_status is not None else -1)
        access_permission = int(raw.access_permission or 2)

        return cls(
            group_id=raw.group_id or raw.id or "",
            title=raw.title,
            access_code=raw.access_code,
            share_url=raw.share_url,
            teacher_id=raw.teacher_id,
            creator_umu_id=raw.umu_id,
            creator_username=raw.username,
            head_img=raw.head_img,
            bg_img=raw.bg_img,
            start_time=start_ts,
            end_time=end_ts,
            create_time=create_ts,
            update_time=update_ts,
            start_time_readable=format_timestamp_beijing(start_ts),
            end_time_readable=format_timestamp_beijing(end_ts),
            create_time_readable=format_timestamp_beijing(create_ts),
            update_time_readable=format_timestamp_beijing(update_ts),
            session_count=int(raw.session_count or 0),
            participant_num=int(raw.partticipate_num or 0),
            finish_num=int(raw.finish_num or 0),
            learning_time=raw.learning_time or raw.vlt or raw.weike_time or "0",
            tags=raw.tags or [],
            audit_status=audit_status,
            audit_status_text=get_course_audit_status_text(audit_status),
            is_course_in_lib=int(raw.is_course_in_lib or 0),
            access_permission=access_permission,
            access_permission_text=get_course_access_permission_text(access_permission),
            source=raw.source or "inner",
            has_group_report=int(raw.has_group_report or 0),
            u_course_score=int(raw.u_course_score or 0),
            first_learning_time=first_ts,
            last_learning_time=last_ts,
            first_learning_time_readable=format_timestamp_beijing(first_ts),
            last_learning_time_readable=format_timestamp_beijing(last_ts),
        )


class AdminCourseListPageInfo(BaseModel):
    """UMU 原始课程列表分页信息."""

    model_config = ConfigDict(populate_by_name=True)

    list_total_num: int = Field(..., description="符合条件的课程总数")
    total_page_num: int = Field(..., description="总页数")
    current_page: int = Field(..., description="当前页码")
    size: int = Field(..., description="当前页大小")


class AdminCourseListPagination(BaseModel):
    """MCP 标准化课程列表分页信息."""

    model_config = ConfigDict(populate_by_name=True)

    total_all: int = Field(..., description="符合条件的课程总数")
    current_page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="当前页大小")


class AdminCourseListData(BaseModel):
    """MCP 标准化课程列表数据."""

    model_config = ConfigDict(populate_by_name=True)

    courses: list[AdminCourse] = Field(..., description="课程列表")
    total: int = Field(..., description="本次返回课程数量")
    pagination: AdminCourseListPagination = Field(..., description="分页信息")


class AdminCourseListResponse(BaseModel):
    """MCP 标准化课程列表响应."""

    model_config = ConfigDict(populate_by_name=True)

    success: bool
    data: AdminCourseListData
    error_code: str = ""
    error_message: str = ""
    suggested_action: str = ""
    next_action: Literal["proceed", "needs_enrollment", "needs_user_input", "lesson_completed"] = (
        "proceed"
    )


class AdminLearningProgramRaw(BaseModel):
    """UMU 原始学习项目对象.

    对应 /ajax/enterprise/getReportProgramList 响应中 `data.list[]` 的单个元素。
    字段名和类型均保持原始接口返回形态。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = ""
    creater_id: str = ""
    program_title: str = ""
    desc: str = ""
    head_img: str = ""
    ctime: str = "0"
    access_permission: str = "2"
    create_time: str = ""
    username: str = ""
    umu_id: str = ""
    share_url: str = ""
    access_code: str = ""
    group_num: str = "0"
    participate_num: int = 0
    partticipate_num: int = 0
    assignment_count: Any = "0"
    module_num: str = "0"
    enterprise_groups: list[str] = Field(default_factory=list)
    enterprise_departments: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    is_in_program_lib: int = 0
    category_name: list[str] = Field(default_factory=list)
    enterprise_id: str = ""


class AdminLearningProgram(BaseModel):
    """Admin MCP 标准化学习项目对象.

    对应 adm_list_learning_programs 返回的 `data.programs[]` 单个元素。
    """

    model_config = ConfigDict(populate_by_name=True)

    program_id: str = Field(default="", description="学习项目 ID")
    title: str = Field(default="", description="学习项目标题")
    desc: str = Field(default="", description="学习项目介绍")
    head_img: str = Field(default="", description="封面图 URL")
    create_time: int = Field(default=0, description="创建时间，Unix 时间戳（秒）")
    create_time_readable: str = Field(default="", description="创建时间，北京时间字符串")
    creator_umu_id: str = Field(default="", description="创建者 UMU 用户 ID")
    creator_username: str = Field(default="", description="创建者用户名")
    share_url: str = Field(default="", description="分享链接")
    access_code: str = Field(default="", description="访问码")
    group_num: int = Field(default=0, description="课程/分组数量")
    participate_num: int = Field(default=0, description="参与人数")
    assignment_count: int = Field(default=0, description="作业/任务数量")
    module_num: int = Field(default=0, description="模块数量")
    enterprise_groups: list[str] = Field(default_factory=list, description="企业分组列表")
    enterprise_departments: list[str] = Field(default_factory=list, description="企业部门列表")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    is_in_program_lib: int = Field(default=0, description="是否在企业知识库，0=否，1=是")
    category_name: list[str] = Field(default_factory=list, description="分类路径名称列表")
    enterprise_id: str = Field(default="", description="企业 ID")
    access_permission: int = Field(default=2, description="权限码")
    access_permission_text: str = Field(default="", description="权限码人读文本")

    @classmethod
    def from_raw(cls, raw: AdminLearningProgramRaw) -> "AdminLearningProgram":
        """从原始 UMU 学习项目对象构造标准化对象."""
        create_ts = int(raw.ctime or 0)
        access_permission = int(raw.access_permission or 2)

        return cls(
            program_id=raw.id,
            title=raw.program_title,
            desc=raw.desc,
            head_img=raw.head_img,
            create_time=create_ts,
            create_time_readable=format_timestamp_beijing(create_ts),
            creator_umu_id=raw.creater_id or raw.umu_id,
            creator_username=raw.username,
            share_url=raw.share_url,
            access_code=raw.access_code,
            group_num=int(raw.group_num or 0),
            participate_num=int(raw.participate_num or raw.partticipate_num or 0),
            assignment_count=int(raw.assignment_count or 0),
            module_num=int(raw.module_num or 0),
            enterprise_groups=raw.enterprise_groups,
            enterprise_departments=raw.enterprise_departments,
            tags=raw.tags,
            is_in_program_lib=raw.is_in_program_lib,
            category_name=raw.category_name,
            enterprise_id=raw.enterprise_id,
            access_permission=access_permission,
            access_permission_text=get_course_access_permission_text(access_permission),
        )


class AdminLearningProgramListPagination(BaseModel):
    """MCP 标准化学习项目列表分页信息."""

    model_config = ConfigDict(populate_by_name=True)

    total_all: int = Field(default=0, description="符合条件的总数量")
    current_page: int = Field(default=1, description="当前页码")
    page_size: int = Field(default=20, description="每页数量")


class AdminLearningProgramListData(BaseModel):
    """MCP 标准化学习项目列表数据."""

    model_config = ConfigDict(populate_by_name=True)

    programs: list[AdminLearningProgram] = Field(..., description="学习项目列表")
    total: int = Field(..., description="本次返回数量")
    pagination: AdminLearningProgramListPagination = Field(..., description="分页信息")


# ---------------------------------------------------------------------------
# 任务明细
# ---------------------------------------------------------------------------


class UserTaskRaw(BaseModel):
    """UMU 原始任务明细对象.

    对应 /uapi/v1/dashboard/user-task-list 响应中 `data.list[]` 的单个元素。
    字段名和类型均保持原始接口返回形态。
    """

    model_config = ConfigDict(populate_by_name=True)

    learning_time: str = ""
    vlt: str = ""
    first_learning_time: str = ""
    last_learning_time: str = ""
    learn_status: int = 0
    finish_time: int = 0
    assign_time: int = 0
    due_time: int = 0
    student: dict[str, Any] = Field(default_factory=dict)
    operator: dict[str, Any] = Field(default_factory=dict)
    task_obj: dict[str, Any] = Field(default_factory=dict)
    assign_obj: dict[str, Any] = Field(default_factory=dict)
    task_obj_id: str = ""


class UserTask(BaseModel):
    """Admin MCP 标准化任务明细对象.

    对应 adm_list_user_tasks 返回的 `data.tasks[]` 单个元素。
    在原始字段基础上做了类型转换和补充计算字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    task_obj_id: str = Field(default="", description="任务对象关系 ID")
    learning_time: str = Field(default="", description="学习时长")
    vlt: str = Field(default="", description="视频学习时长")
    first_learning_time: int = Field(default=0, description="首次学习时间，Unix 时间戳（秒）")
    first_learning_time_readable: str = Field(
        default="", description="首次学习时间，北京时间字符串"
    )
    last_learning_time: int = Field(default=0, description="最后学习时间，Unix 时间戳（秒）")
    last_learning_time_readable: str = Field(default="", description="最后学习时间，北京时间字符串")
    learn_status: int = Field(default=0, description="学习状态码")
    learn_status_text: str = Field(default="", description="学习状态人读文本")
    finish_time: int = Field(default=0, description="完成时间，Unix 时间戳（秒）")
    finish_time_readable: str = Field(default="", description="完成时间，北京时间字符串")
    assign_time: int = Field(default=0, description="分配时间，Unix 时间戳（秒）")
    assign_time_readable: str = Field(default="", description="分配时间，北京时间字符串")
    due_time: int = Field(default=0, description="到期时间，Unix 时间戳（秒），0 表示未指定")
    due_time_readable: str = Field(default="", description="到期时间，北京时间字符串")
    is_overdue: bool = Field(default=False, description="是否逾期完成")
    student_umu_id: str = Field(default="", description="学员 umu_id")
    student_name: str = Field(default="", description="学员姓名")
    student_home_url: str = Field(default="", description="学员主页链接")
    student_groups: list[str] = Field(default_factory=list, description="学员所属分组")
    operator_umu_id: str = Field(default="", description="分配者 umu_id")
    operator_name: str = Field(default="", description="分配者姓名")
    operator_groups: list[str] = Field(default_factory=list, description="分配者所属分组")
    obj_id: str = Field(default="", description="任务对象 ID")
    task_name: str = Field(default="", description="任务名称")
    obj_type: int = Field(default=0, description="任务类型码")
    obj_type_text: str = Field(default="", description="任务类型人读文本")
    session_type: str = Field(default="", description="小节类型")
    course_name: str = Field(default="", description="课程名称")
    course_id: str = Field(default="", description="课程 ID")
    task_url: str = Field(default="", description="任务链接")
    share_url: str = Field(default="", description="分享链接")
    assign_obj_id: str = Field(default="", description="分配对象 ID")
    assign_obj_type: str = Field(default="", description="分配对象类型")
    assign_obj_name: str = Field(default="", description="分配对象名称")

    @classmethod
    def from_raw(cls, raw: UserTaskRaw) -> "UserTask":
        """从原始 UMU 任务明细对象构造标准化对象."""
        first_ts = int(raw.first_learning_time or 0)
        last_ts = int(raw.last_learning_time or 0)
        finish_ts = int(raw.finish_time or 0)
        assign_ts = int(raw.assign_time or 0)
        due_ts = int(raw.due_time or 0)

        student = raw.student or {}
        operator = raw.operator or {}
        task_obj = raw.task_obj or {}
        assign_obj = raw.assign_obj or {}

        obj_type = int(task_obj.get("obj_type") or 0)
        learn_status = int(raw.learn_status or 0)

        obj_type_map = {1: "小节", 2: "课程", 3: "学习项目"}
        learn_status_map = {0: "待学习", 1: "学习中", 2: "按时完成", 3: "逾期完成"}

        is_overdue = False
        if learn_status == 3:
            is_overdue = True
        elif learn_status == 2 and finish_ts > 0 and due_ts > 0 and finish_ts > due_ts:
            is_overdue = True

        return cls(
            task_obj_id=raw.task_obj_id,
            learning_time=raw.learning_time,
            vlt=raw.vlt,
            first_learning_time=first_ts,
            first_learning_time_readable=format_timestamp_beijing(first_ts),
            last_learning_time=last_ts,
            last_learning_time_readable=format_timestamp_beijing(last_ts),
            learn_status=learn_status,
            learn_status_text=learn_status_map.get(learn_status, f"未知({learn_status})"),
            finish_time=finish_ts,
            finish_time_readable=format_timestamp_beijing(finish_ts),
            assign_time=assign_ts,
            assign_time_readable=format_timestamp_beijing(assign_ts),
            due_time=due_ts,
            due_time_readable=format_timestamp_beijing(due_ts) if due_ts else "",
            is_overdue=is_overdue,
            student_umu_id=str(student.get("umu_id", "")),
            student_name=student.get("user_name", "") or "",
            student_home_url=student.get("home_url", "") or "",
            student_groups=student.get("enterprise_groups", []) or [],
            operator_umu_id=str(operator.get("umu_id", "")),
            operator_name=operator.get("user_name", "") or "",
            operator_groups=operator.get("enterprise_groups", []) or [],
            obj_id=str(task_obj.get("obj_id", "")),
            task_name=task_obj.get("task_name", "") or "",
            obj_type=obj_type,
            obj_type_text=obj_type_map.get(obj_type, f"未知({obj_type})"),
            session_type=str(task_obj.get("session_type", "")),
            course_name=task_obj.get("course_name", "") or "",
            course_id=str(task_obj.get("course_id", "")),
            task_url=task_obj.get("task_url", "") or "",
            share_url=task_obj.get("share_url", "") or "",
            assign_obj_id=str(assign_obj.get("id", "")),
            assign_obj_type=str(assign_obj.get("type", "")),
            assign_obj_name=assign_obj.get("name", "") or "",
        )


class UserTaskListPageInfo(BaseModel):
    """UMU 原始任务明细分页信息."""

    model_config = ConfigDict(populate_by_name=True)

    list_total_num: int = Field(..., description="符合条件的任务总数")
    total_page_num: int = Field(..., description="总页数")
    current_page: int = Field(..., description="当前页码")
    size: int = Field(..., description="当前页大小")


class UserTaskListPagination(BaseModel):
    """MCP 标准化任务明细分页信息."""

    model_config = ConfigDict(populate_by_name=True)

    total_all: int = Field(..., description="符合条件的任务总数")
    current_page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="当前页大小")


class UserTaskListData(BaseModel):
    """MCP 标准化任务明细列表数据."""

    model_config = ConfigDict(populate_by_name=True)

    tasks: list[UserTask] = Field(..., description="任务明细列表")
    total: int = Field(..., description="本次返回任务数量")
    pagination: UserTaskListPagination = Field(..., description="分页信息")


class UserTaskListResponse(BaseModel):
    """MCP 标准化任务明细列表响应."""

    model_config = ConfigDict(populate_by_name=True)

    success: bool
    data: UserTaskListData
    error_code: str = ""
    error_message: str = ""
    suggested_action: str = ""
    next_action: Literal["proceed", "needs_enrollment", "needs_user_input", "lesson_completed"] = (
        "proceed"
    )
