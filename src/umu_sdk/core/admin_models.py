"""Admin MCP 账号管理相关 Pydantic 模型.

本模块将账号清单接口的数据结构代码化，供后续数据处理、类型检查
和序列化使用。字段说明详见 docs/admin/account-data-dictionary.md。
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Literal

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
    account_joining_time: int = Field(
        default=0, description="账号加入时间，Unix 时间戳（秒）"
    )
    account_joining_time_readable: str = Field(
        default="", description="账号加入时间，北京时间字符串"
    )
    first_login_time: int = Field(
        default=0, description="首次登录时间，Unix 时间戳（秒）"
    )
    first_login_time_readable: str = Field(
        default="", description="首次登录时间，北京时间字符串"
    )
    last_login_time: int = Field(
        default=0, description="最后登录时间，Unix 时间戳（秒）"
    )
    last_login_time_readable: str = Field(
        default="", description="最后登录时间，北京时间字符串"
    )

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
    next_action: Literal["proceed", "needs_enrollment", "needs_user_input", "lesson_completed"] = "proceed"


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
    enterprise_departments: list[str] = Field(
        default_factory=list, description="所属部门"
    )
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
    last_learning_time: int = Field(
        default=0, description="最后学习时间，Unix 时间戳（秒）"
    )
    last_learning_time_readable: str = Field(
        default="", description="最后学习时间，北京时间字符串"
    )
    sum_learning_time: str = Field(default="", description="累计学习时长")
    group_required_session_total_count: int = Field(
        default=0, description="课程必修小节总数"
    )
    group_required_session_finished_count: int = Field(
        default=0, description="已完成必修小节数"
    )
    group_completion_rate: float = Field(default=0.0, description="课程完成率（0-1）")
    group_overall_completion_rate: float = Field(
        default=0.0, description="整体完成率（0-1）"
    )
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
            group_overall_completion_time_readable=format_timestamp_beijing(
                overall_completion_ts
            ),
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
    next_action: Literal["proceed", "needs_enrollment", "needs_user_input", "lesson_completed"] = "proceed"


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
    next_action: Literal["proceed", "needs_enrollment", "needs_user_input", "lesson_completed"] = "proceed"
