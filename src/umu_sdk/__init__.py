"""UMU SDK - Python SDK for UMU LMS Platform.

基于逆向分析构建的 Python SDK，提供课程、资源、用户等管理功能，
并支持作为 MCP (Model Context Protocol) 服务暴露给 AI 调用。

Usage:
    from umu_sdk import UMUClient

    client = UMUClient(base_url="https://www.umu.cn")
    client.login("username", "password")

    courses = client.courses.list()
    for course in courses.data:
        print(course.title)
"""

from .core.client import UMUClient
from .core.auth import AuthManager
from .core.encrypt import encrypt_password, decrypt_password, verify_encryption
from .core.errors import (
    UMUError,
    AuthenticationError,
    ValidationError,
    RateLimitError,
    ServerError,
)
from .core.models import (
    Course,
    CreateCourseRequest,
    UpdateCourseRequest,
    ListCoursesParams,
    PaginatedResponse,
)

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("umu-skills")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"

__all__ = [
    "UMUClient",
    "AuthManager",
    "encrypt_password",
    "decrypt_password",
    "verify_encryption",
    "UMUError",
    "AuthenticationError",
    "ValidationError",
    "RateLimitError",
    "ServerError",
    "Course",
    "CreateCourseRequest",
    "UpdateCourseRequest",
    "ListCoursesParams",
    "PaginatedResponse",
]
