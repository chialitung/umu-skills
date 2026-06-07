"""UMU SDK 异常类."""


class UMUError(Exception):
    """UMU SDK 基础异常."""

    def __init__(
        self,
        message: str,
        code: str = "UNKNOWN_ERROR",
        status: int | None = None,
        request_id: str | None = None,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status = status
        self.request_id = request_id
        self.details = details or {}

    def __str__(self) -> str:
        parts = [f"[{self.code}] {self.args[0]}"]
        if self.status:
            parts.append(f"(HTTP {self.status})")
        if self.request_id:
            parts.append(f"RequestId: {self.request_id}")
        return " ".join(parts)


class EnvironmentMismatchError(UMUError):
    """环境不匹配错误.

    当请求的域名与 SDK 初始化时配置的环境不一致时抛出.
    """

    def __init__(self, message: str):
        super().__init__(message, code="ENVIRONMENT_MISMATCH")


class AuthenticationError(UMUError):
    """认证错误."""

    def __init__(self, message: str = "认证失败"):
        super().__init__(message, code="AUTHENTICATION_ERROR", status=401)


class ValidationError(UMUError):
    """参数校验错误."""

    def __init__(self, message: str = "参数校验失败", details: dict | None = None):
        super().__init__(message, code="VALIDATION_ERROR", status=422, details=details)


class RateLimitError(UMUError):
    """请求频率限制错误."""

    def __init__(self, message: str = "请求过于频繁"):
        super().__init__(message, code="RATE_LIMITED", status=429)


class ServerError(UMUError):
    """服务器内部错误."""

    def __init__(self, message: str = "服务器内部错误", status: int = 500):
        super().__init__(message, code="SERVER_ERROR", status=status)


class NetworkError(UMUError):
    """网络连接错误."""

    def __init__(self, message: str = "网络连接失败"):
        super().__init__(message, code="NETWORK_ERROR")
