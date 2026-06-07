"""UMU Skills SDK Core.

SDK 核心层 — HTTP 客户端、认证、加密、数据模型、错误处理.
"""

from .core.client import UMUClient
from .core.auth import AuthManager
from .core.encrypt import encrypt_password, decrypt_password, verify_encryption
from .core.errors import (
    UMUError,
    EnvironmentMismatchError,
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

__all__ = [
    "UMUClient",
    "AuthManager",
    "encrypt_password",
    "decrypt_password",
    "verify_encryption",
    "UMUError",
    "EnvironmentMismatchError",
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
