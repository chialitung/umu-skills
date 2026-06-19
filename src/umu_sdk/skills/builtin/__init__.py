# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""内置示例 Skill.

本包包含若干高阶 Skill 示例，用于演示如何基于 @skill 装饰器编排
Teacher / Student / Admin 子 MCP 的原子工具。
"""

from .admin_course_permissions import (
    add_course_access_accounts_admin,
    cancel_course_access_permissions_admin,
    cancel_course_auto_close_admin,
    get_course_access_list_admin,
    get_course_access_permission_admin,
    remove_course_access_accounts_admin,
    search_course_access_accounts_admin,
    set_course_access_permission_admin,
    set_course_auto_close_admin,
)
from .admin_learning_programs_personal import (
    add_program_access_accounts_admin,
    cancel_program_access_permissions_admin,
    get_program_access_list_admin,
    get_program_access_permission_admin,
    list_admin_personal_learning_programs,
    list_cooperated_learning_programs_admin,
    list_enrolled_learning_programs_admin,
    list_owned_learning_programs_admin,
    remove_program_access_accounts_admin,
    search_program_access_accounts_admin,
    set_program_access_permission_admin,
)
from .admin_tasks import get_user_tasks
from .admin_teaching_records import get_teaching_records
from .teacher_course_permissions import (
    add_course_access_accounts,
    cancel_course_access_permissions,
    cancel_course_auto_close,
    get_course_access_list,
    get_course_access_permission,
    remove_course_access_accounts,
    search_course_access_accounts,
    set_course_access_permission,
    set_course_auto_close,
)
from .teacher_learning_programs import (
    add_program_access_accounts,
    cancel_program_access_permissions,
    get_program_access_list,
    get_program_access_permission,
    list_cooperated_learning_programs,
    list_enrolled_learning_programs,
    list_owned_learning_programs,
    list_teacher_learning_programs,
    remove_program_access_accounts,
    search_program_access_accounts,
    set_program_access_permission,
)

__all__ = [
    "add_course_access_accounts",
    "add_course_access_accounts_admin",
    "add_program_access_accounts",
    "cancel_course_access_permissions",
    "cancel_course_access_permissions_admin",
    "cancel_course_auto_close",
    "cancel_course_auto_close_admin",
    "cancel_program_access_permissions",
    "get_course_access_list",
    "get_course_access_list_admin",
    "get_course_access_permission",
    "get_course_access_permission_admin",
    "add_program_access_accounts_admin",
    "cancel_program_access_permissions_admin",
    "get_program_access_list_admin",
    "get_program_access_permission_admin",
    "list_admin_personal_learning_programs",
    "list_cooperated_learning_programs_admin",
    "list_enrolled_learning_programs_admin",
    "list_owned_learning_programs_admin",
    "remove_program_access_accounts_admin",
    "search_program_access_accounts_admin",
    "set_program_access_permission_admin",
    "get_instructors",
    "get_program_access_list",
    "get_program_access_permission",
    "get_teaching_records",
    "get_user_tasks",
    "list_cooperated_learning_programs",
    "list_enrolled_learning_programs",
    "list_owned_learning_programs",
    "list_teacher_learning_programs",
    "remove_course_access_accounts",
    "remove_course_access_accounts_admin",
    "remove_program_access_accounts",
    "search_course_access_accounts",
    "search_course_access_accounts_admin",
    "search_program_access_accounts",
    "set_course_access_permission",
    "set_course_access_permission_admin",
    "set_course_auto_close",
    "set_course_auto_close_admin",
    "set_program_access_permission",
]
