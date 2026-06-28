# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""内置示例 Skill.

本包包含若干高阶 Skill 示例，用于演示如何基于 @skill 装饰器编排
Teacher / Student / Admin 子 MCP 的原子工具。
"""

from .admin_courses import (
    cancel_course_auto_close_admin,
    get_course_auto_close_admin,
    set_course_auto_close_admin,
)
from .admin_learning_programs_personal import (
    list_admin_personal_learning_programs,
    list_cooperated_learning_programs_admin,
    list_enrolled_learning_programs_admin,
    list_owned_learning_programs_admin,
)
from .admin_tasks import get_user_tasks
from .admin_teaching_records import get_teaching_records
from .course_permissions import (
    add_course_access_accounts,
    cancel_course_access_permissions,
    cancel_course_auto_close,
    get_course_access_list,
    get_course_access_permission,
    get_course_auto_close,
    remove_course_access_accounts,
    search_course_access_accounts,
    set_course_access_permission,
    set_course_auto_close,
)
from .program_permissions import (
    add_program_access_accounts,
    cancel_program_access_permissions,
    get_program_access_list,
    get_program_access_permission,
    remove_program_access_accounts,
    search_program_access_accounts,
    set_program_access_permission,
)
from .teacher_learning_programs import (
    list_cooperated_learning_programs,
    list_enrolled_learning_programs,
    list_owned_learning_programs,
    list_teacher_learning_programs,
)

__all__ = [
    "add_course_access_accounts",
    "add_program_access_accounts",
    "cancel_course_access_permissions",
    "cancel_course_auto_close",
    "cancel_course_auto_close_admin",
    "cancel_program_access_permissions",
    "get_course_access_list",
    "get_course_access_permission",
    "get_course_auto_close",
    "get_course_auto_close_admin",
    "get_program_access_list",
    "get_program_access_permission",
    "get_teaching_records",
    "get_user_tasks",
    "list_admin_personal_learning_programs",
    "list_cooperated_learning_programs",
    "list_cooperated_learning_programs_admin",
    "list_enrolled_learning_programs",
    "list_enrolled_learning_programs_admin",
    "list_owned_learning_programs",
    "list_owned_learning_programs_admin",
    "list_teacher_learning_programs",
    "remove_course_access_accounts",
    "remove_program_access_accounts",
    "search_course_access_accounts",
    "search_program_access_accounts",
    "set_course_access_permission",
    "set_course_auto_close",
    "set_course_auto_close_admin",
    "set_program_access_permission",
]
