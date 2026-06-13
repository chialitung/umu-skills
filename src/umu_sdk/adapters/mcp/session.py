"""MCP 会话管理器 — 支持多用户会话隔离.

每个会话拥有独立的 UMUClient 实例（含独立的 httpx.Client 和 Cookie Jar），
确保多用户并发使用时的登录状态互不干扰。

Usage:
    manager = SessionManager(base_url="https://www.umu.cn")
    session = await manager.create_session()
    # 在指定会话中登录
    session.client.login("user1", "pass1")
    # 后续通过 session_id 获取已认证的客户端
    client = manager.get_session_sync(session.session_id).client
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

from ...core.client import UMUClient


@dataclass
class SessionInfo:
    """会话元数据（无敏感信息，用于列出）."""

    session_id: str
    username: str | None
    credential_source: str | None
    created_at: float
    last_used_at: float
    is_authenticated: bool


@dataclass
class UMUSession:
    """UMU 用户会话 — 封装独立的 UMUClient."""

    session_id: str
    client: UMUClient
    username: str | None = None
    credential_source: str | None = None
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        """更新最后使用时间."""
        self.last_used_at = time.time()

    def to_info(self) -> SessionInfo:
        """转换为会话信息（不含敏感数据）."""
        return SessionInfo(
            session_id=self.session_id,
            username=self.username,
            credential_source=self.credential_source,
            created_at=self.created_at,
            last_used_at=self.last_used_at,
            is_authenticated=self.client.auth.is_authenticated(),
        )


class SessionManager:
    """会话管理器 — 协程安全的会话池.

    支持：
    - 创建/获取/销毁会话
    - TTL 自动过期清理（默认 24 小时）
    - 最大会话数限制（默认 100）
    - 并发安全（asyncio.Lock）
    """

    DEFAULT_SESSION_ID = "default"
    DEFAULT_MAX_SESSIONS = 100
    DEFAULT_SESSION_TTL = 3600 * 24  # 24 小时

    def __init__(
        self,
        base_url: str,
        endpoint_overrides: dict[str, str] | None = None,
        session_ttl: int = DEFAULT_SESSION_TTL,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
    ):
        self.base_url = base_url
        self.endpoint_overrides = endpoint_overrides or {}
        self.session_ttl = session_ttl
        self.max_sessions = max_sessions

        self._sessions: dict[str, UMUSession] = {}
        self._lock = asyncio.Lock()

    def _create_client(self) -> UMUClient:
        """创建新的 UMUClient 实例."""
        return UMUClient(
            base_url=self.base_url,
            endpoint_overrides=self.endpoint_overrides,
        )

    def _generate_session_id(self) -> str:
        """生成短格式会话 ID（uuid 前 16 位）."""
        return str(uuid.uuid4())[:16]

    async def create_session(
        self,
        username: str | None = None,
        password: str | None = None,
    ) -> UMUSession:
        """创建新会话，可选自动登录.

        Args:
            username: 可选用户名，提供则尝试自动登录
            password: 可选密码

        Returns:
            新创建的会话

        Raises:
            RuntimeError: 会话池已满（超过 max_sessions）
        """
        async with self._lock:
            if len(self._sessions) >= self.max_sessions:
                raise RuntimeError(
                    f"会话池已满（最大 {self.max_sessions} 个），"
                    "请先销毁不用的会话或等待过期"
                )

        session_id = self._generate_session_id()
        client = self._create_client()
        session = UMUSession(
            session_id=session_id,
            client=client,
            username=username,
        )

        if username and password:
            try:
                client.login(username, password)
                session.username = username
            except Exception:
                # 登录失败也保留会话，允许用户重试
                pass

        async with self._lock:
            self._sessions[session_id] = session

        return session

    async def get_session(self, session_id: str) -> UMUSession | None:
        """获取会话，自动更新最后使用时间并检查 TTL.

        Args:
            session_id: 会话 ID

        Returns:
            会话对象，不存在或已过期则返回 None
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None

            # 检查 TTL
            if time.time() - session.last_used_at > self.session_ttl:
                self._destroy_session_unlocked(session_id)
                return None

            session.touch()
            return session

    def get_session_sync(self, session_id: str) -> UMUSession | None:
        """同步获取会话（用于已有 event loop 的 async tool 内部）.

        注意：此函数不获取锁，依赖调用方场景（单线程 event loop）。
        TTL 检查在此版本中不自动销毁，仅返回 None。

        Args:
            session_id: 会话 ID

        Returns:
            会话对象，不存在则返回 None
        """
        session = self._sessions.get(session_id)
        if session is None:
            return None

        # 检查 TTL
        if time.time() - session.last_used_at > self.session_ttl:
            return None

        session.touch()
        return session

    async def destroy_session(self, session_id: str) -> bool:
        """销毁指定会话.

        Args:
            session_id: 会话 ID

        Returns:
            是否成功销毁
        """
        async with self._lock:
            return self._destroy_session_unlocked(session_id)

    def _destroy_session_unlocked(self, session_id: str) -> bool:
        """内部销毁（必须在锁保护下调用）."""
        session = self._sessions.pop(session_id, None)
        if session is not None:
            try:
                session.client.close()
            except Exception:
                pass
            return True
        return False

    async def list_sessions(self) -> list[SessionInfo]:
        """列出所有活跃会话.

        Returns:
            会话信息列表（不含敏感数据）
        """
        async with self._lock:
            return [s.to_info() for s in self._sessions.values()]

    async def cleanup_expired(self) -> int:
        """清理过期会话.

        Returns:
            清理的会话数量
        """
        now = time.time()
        expired_ids: list[str] = []

        async with self._lock:
            for sid, session in list(self._sessions.items()):
                if now - session.last_used_at > self.session_ttl:
                    expired_ids.append(sid)

            for sid in expired_ids:
                self._destroy_session_unlocked(sid)

        return len(expired_ids)

    async def login_session(
        self,
        session_id: str,
        username: str,
        password: str,
        credential_source: str | None = None,
    ) -> str:
        """对指定会话执行登录.

        Args:
            session_id: 会话 ID
            username: 用户名
            password: 密码
            credential_source: 可选的凭证来源标记，用于可观测性

        Returns:
            认证 Token

        Raises:
            ValueError: 会话不存在或已过期
        """
        session = await self.get_session(session_id)
        if session is None:
            raise ValueError(f"会话不存在或已过期: {session_id}")

        token = session.client.login(username, password)
        session.username = username
        session.credential_source = credential_source
        return token

    def close_all(self) -> None:
        """关闭所有会话并清空会话池."""
        for session in list(self._sessions.values()):
            try:
                session.client.close()
            except Exception:
                pass
        self._sessions.clear()

    def get_client(self, session_id: str | None = None) -> UMUClient:
        """获取指定会话的客户端实例.

        Args:
            session_id: 会话 ID，None 则使用默认会话

        Returns:
            UMUClient 实例

        Raises:
            RuntimeError: 会话不存在
        """
        sid = session_id or self.DEFAULT_SESSION_ID
        session = self._sessions.get(sid)
        if session is None:
            raise RuntimeError(f"会话不存在: {sid}")
        session.touch()
        return session.client
