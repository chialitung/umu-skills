"""SessionManager 单元测试."""

from __future__ import annotations

import asyncio

import pytest

from umu_sdk.adapters.mcp.session import SessionManager


@pytest.fixture
def manager():
    """创建测试用的 SessionManager."""
    return SessionManager(
        base_url="https://www.umu.cn",
        session_ttl=3600,
        max_sessions=10,
    )


@pytest.mark.asyncio
async def test_create_session(manager: SessionManager):
    """测试创建会话."""
    session = await manager.create_session()
    assert session.session_id is not None
    assert len(session.session_id) == 16
    assert session.client is not None
    assert session.username is None
    assert not session.client.auth.is_authenticated()


@pytest.mark.asyncio
async def test_get_session(manager: SessionManager):
    """测试获取会话."""
    session = await manager.create_session()
    retrieved = await manager.get_session(session.session_id)
    assert retrieved is not None
    assert retrieved.session_id == session.session_id
    assert retrieved.client == session.client


@pytest.mark.asyncio
async def test_get_nonexistent_session(manager: SessionManager):
    """测试获取不存在的会话返回 None."""
    result = await manager.get_session("nonexistent123")
    assert result is None


@pytest.mark.asyncio
async def test_session_isolation(manager: SessionManager):
    """测试会话隔离 — 两个会话互不干扰."""
    session1 = await manager.create_session()
    session2 = await manager.create_session()

    assert session1.session_id != session2.session_id
    assert session1.client != session2.client
    assert session1.client.http != session2.client.http


@pytest.mark.asyncio
async def test_destroy_session(manager: SessionManager):
    """测试销毁会话."""
    session = await manager.create_session()
    sid = session.session_id

    success = await manager.destroy_session(sid)
    assert success is True

    # 销毁后应无法获取
    retrieved = await manager.get_session(sid)
    assert retrieved is None


@pytest.mark.asyncio
async def test_destroy_nonexistent_session(manager: SessionManager):
    """测试销毁不存在的会话返回 False."""
    success = await manager.destroy_session("nonexistent")
    assert success is False


@pytest.mark.asyncio
async def test_list_sessions(manager: SessionManager):
    """测试列出会话."""
    s1 = await manager.create_session(username="user1")
    s2 = await manager.create_session(username="user2")

    sessions = await manager.list_sessions()
    assert len(sessions) == 2

    ids = {s.session_id for s in sessions}
    assert s1.session_id in ids
    assert s2.session_id in ids

    # 验证脱敏信息
    for s in sessions:
        assert hasattr(s, "session_id")
        assert hasattr(s, "username")
        assert hasattr(s, "is_authenticated")
        assert not hasattr(s, "client")  # SessionInfo 不应包含 client


@pytest.mark.asyncio
async def test_session_ttl_expiration(manager: SessionManager):
    """测试会话 TTL 过期."""
    manager.session_ttl = 0  # 立即过期

    session = await manager.create_session()
    sid = session.session_id

    # 将最后使用时间回拨，避免在同一时刻创建和获取导致时间差为 0
    session.last_used_at -= 1

    # 立即获取应返回 None（已过期）
    retrieved = await manager.get_session(sid)
    assert retrieved is None


@pytest.mark.asyncio
async def test_max_sessions_limit():
    """测试最大会话数限制."""
    manager = SessionManager(
        base_url="https://www.umu.cn",
        max_sessions=2,
    )

    await manager.create_session()
    await manager.create_session()

    # 第三个应失败
    with pytest.raises(RuntimeError, match="会话池已满"):
        await manager.create_session()


@pytest.mark.asyncio
async def test_concurrent_session_creation():
    """测试并发创建会话."""
    manager = SessionManager(
        base_url="https://www.umu.cn",
        max_sessions=20,
    )

    tasks = [manager.create_session() for _ in range(10)]
    sessions = await asyncio.gather(*tasks)

    assert len(sessions) == 10
    ids = [s.session_id for s in sessions]
    assert len(set(ids)) == 10  # 全部唯一


@pytest.mark.asyncio
async def test_cleanup_expired(manager: SessionManager):
    """测试清理过期会话."""
    manager.session_ttl = 0

    s1 = await manager.create_session()
    s2 = await manager.create_session()
    # 将最后使用时间回拨，确保在 Windows 低精度计时下也视为过期
    s1.last_used_at -= 1
    s2.last_used_at -= 1

    count = await manager.cleanup_expired()
    assert count == 2

    # 清理后列表为空
    sessions = await manager.list_sessions()
    assert len(sessions) == 0


@pytest.mark.asyncio
async def test_close_all(manager: SessionManager):
    """测试关闭所有会话."""
    await manager.create_session()
    await manager.create_session()

    manager.close_all()

    sessions = await manager.list_sessions()
    assert len(sessions) == 0
