"""UMU HTTP 客户端和主客户端.

基于 httpx 构建，提供同步/异步请求支持、认证拦截、错误处理、环境验证.
"""

import os
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

# 自动加载 .env 文件（如果存在 python-dotenv）
_venv_loaded = False

def _load_dotenv() -> None:
    """自动检测并加载 .env 文件."""
    global _venv_loaded
    if _venv_loaded:
        return
    try:
        from dotenv import load_dotenv
        # 尝试多个可能的 .env 位置
        candidates = [
            os.path.join(os.getcwd(), ".env"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                load_dotenv(path)
                break
    except ImportError:
        pass  # python-dotenv 未安装，跳过
    finally:
        _venv_loaded = True

_load_dotenv()

from .core.auth import AuthManager
from .core.errors import (
    AuthenticationError,
    EnvironmentMismatchError,
    NetworkError,
    RateLimitError,
    ServerError,
    UMUError,
    ValidationError,
)
from .core.models import LoginCredentials


# 预定义环境配置注册表（支持 future 扩展接口路径覆盖）
ENVIRONMENT_REGISTRY: dict[str, dict[str, Any]] = {
    "prod": {
        "base_url": "https://www.umu.cn",
        "desktop_domain": "www.umu.cn",
        "mobile_domain": "m.umu.cn",
        "endpoint_overrides": {},
    },
}


class UMUClient:
    """UMU API 客户端.

    Usage:
        client = UMUClient(base_url="https://www.umu.cn")
        client.login("username", "password")

        courses = client.get("/api/courses")
        print(courses)

    """

    def __init__(
        self,
        base_url: str,
        auth: LoginCredentials | None = None,
        timeout: float = 30.0,
        retries: int = 3,
        enable_environment_check: bool = True,
        follow_redirects: bool = True,
        environment: str = "default",
        endpoint_overrides: dict[str, str] | None = None,
    ):
        """初始化 UMU 客户端.

        Args:
            base_url: UMU 基础 URL，如 https://www.umu.cn
            auth: 可选的登录凭据
            timeout: 请求超时（秒）
            retries: 重试次数
            enable_environment_check: 是否启用环境验证
            follow_redirects: 是否跟随重定向
            environment: 环境标识（如 "prod", "default"）
            endpoint_overrides: 接口路径覆盖（用于极少数环境间接口路径不同的情况）
        """
        if not base_url:
            raise ValueError("UMUClient 初始化失败: base_url 不能为空")

        # 验证 URL 格式
        try:
            parsed = urlparse(base_url)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError
        except Exception:
            raise ValueError(f"UMUClient 初始化失败: base_url '{base_url}' 不是有效的 URL")

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.enable_environment_check = enable_environment_check
        self.environment = environment
        self.endpoint_overrides = endpoint_overrides or {}

        # 从 base_url 推断 desktop_domain 和 mobile_domain
        hostname = parsed.hostname or ""
        self.desktop_domain = hostname
        if hostname.startswith("www."):
            self.mobile_domain = "m." + hostname[4:]
        else:
            self.mobile_domain = "m." + hostname

        # 合并内置环境配置的 endpoint_overrides
        env_config = ENVIRONMENT_REGISTRY.get(environment, {})
        builtin_overrides = env_config.get("endpoint_overrides", {})
        # 用户传入的覆盖优先级高于内置配置
        merged_overrides = dict(builtin_overrides)
        merged_overrides.update(self.endpoint_overrides)
        self.endpoint_overrides = merged_overrides

        # 初始化 HTTP 客户端
        self.http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=follow_redirects,
            headers={
                "Accept": "application/json",
                "User-Agent": "umu-sdk-python/0.1.0",
            },
        )

        # 初始化认证管理器
        self.auth = AuthManager(self.http, auth, self.base_url)

        print(f"[UMUClient] 初始化完成，环境: {environment}, 目标: {self.base_url}")
        print(f"[UMUClient] desktop_domain: {self.desktop_domain}, mobile_domain: {self.mobile_domain}")

    def desktop_url(self, path: str) -> str:
        """构建桌面端完整 URL.

        Args:
            path: URL 路径，如 "/uapi/v1/element/list"

        Returns:
            完整 URL，如 "https://www.umu.cn/uapi/v1/element/list"
        """
        path = path if path.startswith("/") else "/" + path
        return f"https://{self.desktop_domain}{path}"

    def mobile_url(self, path: str) -> str:
        """构建移动端完整 URL.

        Args:
            path: URL 路径，如 "/api/session/makeweikestatus"

        Returns:
            完整 URL，如 "https://m.umu.cn/api/session/makeweikestatus"
        """
        path = path if path.startswith("/") else "/" + path
        return f"https://{self.mobile_domain}{path}"

    def endpoint(self, name: str, default_path: str, domain: str = "desktop") -> str:
        """获取接口 URL，支持环境特定的路径覆盖.

        用于极少数情况下，不同环境的接口路径不同（不只是 domain 不同）。
        大部分接口只需用 desktop_url() / mobile_url()。

        Args:
            name: 接口名称（用于查找覆盖配置）
            default_path: 默认路径
            domain: "desktop" 或 "mobile"

        Returns:
            完整 URL
        """
        path = self.endpoint_overrides.get(name, default_path)
        if domain == "mobile":
            return self.mobile_url(path)
        return self.desktop_url(path)

    def login(self, username: str | None = None, password: str | None = None) -> str:
        """登录并获取 Token.

        Args:
            username: 用户名/邮箱/手机号
            password: 明文密码

        Returns:
            认证 Token
        """
        token = self.auth.login(username, password)

        # 登录成功后，将 Cookie 同步到 httpx 客户端
        # httpx 会自动管理 cookies
        return token

    def _validate_environment(self, url: str) -> None:
        """验证请求 URL 是否在允许的环境内."""
        if not self.enable_environment_check:
            return

        try:
            request_hostname = urlparse(url).hostname or ""
            request_hostname = request_hostname.lower()

            # 使用 AuthManager 的 allowed_domains 进行验证
            allowed_domains = self.auth.allowed_domains if self.auth else []
            is_allowed = any(
                request_hostname == allowed or request_hostname.endswith(f".{allowed}")
                for allowed in allowed_domains
            )

            if not is_allowed:
                allowed_str = ", ".join(allowed_domains) if allowed_domains else self.base_url
                raise EnvironmentMismatchError(
                    f"SDK 安全拦截：请求域名 '{request_hostname}' 与初始化环境不匹配。"
                    f"当前允许的环境: {allowed_str}。请求 URL: {url}"
                )
        except EnvironmentMismatchError:
            raise
        except Exception:
            pass  # URL 解析失败时跳过验证

    def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """发送 HTTP 请求（带重试和错误处理）."""
        # 环境验证
        full_url = url if url.startswith("http") else urljoin(self.base_url, url)
        self._validate_environment(full_url)

        # 附加认证信息
        auth_headers = self.auth.get_auth_headers()
        headers = kwargs.pop("headers", {})
        headers.update(auth_headers)

        last_error: Exception | None = None

        for attempt in range(self.retries):
            try:
                print(f"[HTTP] {method.upper()} {url}")
                response = self.http.request(method, url, headers=headers, **kwargs)
                response.raise_for_status()
                print(f"[HTTP] {response.status_code} {url}")
                return response

            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                self._handle_http_error(status, e.response)
                last_error = e

                # 可重试错误
                if status in (429, 502, 503, 504) and attempt < self.retries - 1:
                    import time

                    wait_time = 2**attempt  # 指数退避
                    print(f"[HTTP] 请求失败，{wait_time}s 后重试 ({attempt + 1}/{self.retries})")
                    time.sleep(wait_time)
                    continue

                break

            except EnvironmentMismatchError:
                raise
            except Exception as e:
                last_error = e
                if attempt < self.retries - 1:
                    import time

                    time.sleep(1)
                    continue
                break

        # 所有重试失败
        if isinstance(last_error, UMUError):
            raise last_error
        raise NetworkError(f"请求失败: {last_error}")

    def _handle_http_error(self, status: int, response: httpx.Response) -> None:
        """处理 HTTP 错误状态码."""
        try:
            data = response.json()
        except Exception:
            data = {"message": response.text}

        message = data.get("message") or data.get("error") or data.get("error_message") or "请求失败"

        if status == 401:
            raise AuthenticationError(message)
        elif status == 403:
            raise UMUError(message, code="FORBIDDEN", status=403)
        elif status == 404:
            raise UMUError(message, code="NOT_FOUND", status=404)
        elif status == 422:
            raise ValidationError(message, details=data.get("errors", data))
        elif status == 429:
            raise RateLimitError(message)
        elif status >= 500:
            raise ServerError(message, status=status)
        else:
            raise UMUError(message, code=f"HTTP_{status}", status=status)

    def get(self, url: str, params: dict | None = None, **kwargs: Any) -> Any:
        """GET 请求."""
        response = self._request("GET", url, params=params, **kwargs)
        return response.json()

    def post(self, url: str, data: Any = None, json: Any = None, **kwargs: Any) -> Any:
        """POST 请求."""
        response = self._request("POST", url, data=data, json=json, **kwargs)
        return response.json()

    def put(self, url: str, data: Any = None, json: Any = None, **kwargs: Any) -> Any:
        """PUT 请求."""
        response = self._request("PUT", url, data=data, json=json, **kwargs)
        return response.json()

    def patch(self, url: str, data: Any = None, json: Any = None, **kwargs: Any) -> Any:
        """PATCH 请求."""
        response = self._request("PATCH", url, data=data, json=json, **kwargs)
        return response.json()

    def delete(self, url: str, **kwargs: Any) -> Any:
        """DELETE 请求."""
        response = self._request("DELETE", url, **kwargs)
        if response.status_code == 204:
            return None
        return response.json()

    def close(self) -> None:
        """关闭客户端，释放资源."""
        self.http.close()
        print("[UMUClient] 已关闭")

    def __enter__(self) -> "UMUClient":
        """上下文管理器入口."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """上下文管理器退出."""
        self.close()
