"""Tests for skills.credential_manager."""

from __future__ import annotations

from pathlib import Path

import pytest

from umu_sdk.skills import credential_manager as cm


class _MemoryKeyring:
    """内存中的 keyring 后端，用于测试."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password


@pytest.fixture
def mock_keyring(monkeypatch: pytest.MonkeyPatch) -> _MemoryKeyring:
    """用内存 keyring 替换真实的 keyring 模块."""
    memory = _MemoryKeyring()
    monkeypatch.setattr(
        cm,
        "_get_keyring_password",
        lambda: memory.get_password(cm.KEYRING_SERVICE, cm.KEYRING_USERNAME),
    )
    monkeypatch.setattr(
        cm,
        "_set_keyring_password",
        lambda key: memory.set_password(cm.KEYRING_SERVICE, cm.KEYRING_USERNAME, key),
    )
    return memory


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    """返回一个临时 skill 目录."""
    return tmp_path / "umu"


class TestCredentialManager:
    def test_save_and_load_role_credentials(
        self,
        mock_keyring: _MemoryKeyring,
        skill_dir: Path,
    ) -> None:
        cm.set_role_credentials("teacher", "teacher@test.com", "pass123", skill_dir=skill_dir)

        username, password = cm.get_role_credentials("teacher", skill_dir=skill_dir)
        assert username == "teacher@test.com"
        assert password == "pass123"

    def test_multiple_roles(
        self,
        mock_keyring: _MemoryKeyring,
        skill_dir: Path,
    ) -> None:
        cm.set_role_credentials("teacher", "t", "tpass", skill_dir=skill_dir)
        cm.set_role_credentials("student", "s", "spass", skill_dir=skill_dir)
        cm.set_role_credentials("admin", "a", "apass", skill_dir=skill_dir)

        creds = cm.load_credentials(skill_dir)
        assert set(creds.keys()) == {"teacher", "student", "admin"}
        assert creds["student"]["username"] == "s"
        assert creds["admin"]["password"] == "apass"

    def test_has_role_credentials(
        self,
        mock_keyring: _MemoryKeyring,
        skill_dir: Path,
    ) -> None:
        assert not cm.has_role_credentials("teacher", skill_dir=skill_dir)

        cm.set_role_credentials("teacher", "t", "p", skill_dir=skill_dir)
        assert cm.has_role_credentials("teacher", skill_dir=skill_dir)

    def test_list_configured_roles(
        self,
        mock_keyring: _MemoryKeyring,
        skill_dir: Path,
    ) -> None:
        assert cm.list_configured_roles(skill_dir) == []

        cm.set_role_credentials("teacher", "t", "p", skill_dir=skill_dir)
        cm.set_role_credentials("admin", "a", "p", skill_dir=skill_dir)

        roles = cm.list_configured_roles(skill_dir)
        assert sorted(roles) == ["admin", "teacher"]

    def test_delete_role_credentials(
        self,
        mock_keyring: _MemoryKeyring,
        skill_dir: Path,
    ) -> None:
        cm.set_role_credentials("teacher", "t", "p", skill_dir=skill_dir)
        assert cm.has_role_credentials("teacher", skill_dir=skill_dir)

        cm.delete_role_credentials("teacher", skill_dir=skill_dir)
        assert not cm.has_role_credentials("teacher", skill_dir=skill_dir)

    def test_file_not_exists_returns_empty(
        self,
        mock_keyring: _MemoryKeyring,
        skill_dir: Path,
    ) -> None:
        assert cm.load_credentials(skill_dir) == {}
        assert cm.get_role_credentials("teacher", skill_dir=skill_dir) == (None, None)

    def test_corrupted_file_returns_empty(
        self,
        mock_keyring: _MemoryKeyring,
        skill_dir: Path,
    ) -> None:
        skill_dir.mkdir(parents=True, exist_ok=True)
        cm.get_credentials_path(skill_dir).write_bytes(b"not-valid-fernet-data")

        assert cm.load_credentials(skill_dir) == {}

    def test_fallback_key_file_when_keyring_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        skill_dir: Path,
    ) -> None:
        """模拟 keyring 完全不可用时，应使用 .fernet.key 文件."""
        monkeypatch.setattr(cm, "_get_keyring_password", lambda: None)
        monkeypatch.setattr(cm, "_set_keyring_password", lambda *args, **kwargs: False)

        cm.set_role_credentials("teacher", "t", "p", skill_dir=skill_dir)

        assert (skill_dir / cm.FALLBACK_KEY_FILENAME).exists()

        username, password = cm.get_role_credentials("teacher", skill_dir=skill_dir)
        assert username == "t"
        assert password == "p"

    def test_env_skill_dir_override(
        self,
        mock_keyring: _MemoryKeyring,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        custom_dir = tmp_path / "custom"
        monkeypatch.setenv("UMU_SKILL_DIR", str(custom_dir))

        cm.set_role_credentials("teacher", "t", "p")
        assert cm.get_skill_dir() == custom_dir
        assert cm.has_role_credentials("teacher")


class TestCredentialLoader:
    @pytest.fixture(autouse=True)
    def _isolated_skill_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        """通过环境变量隔离 credential_loader 使用的 skill 目录."""
        skill_dir = tmp_path / "umu"
        monkeypatch.setenv("UMU_SKILL_DIR", str(skill_dir))
        return skill_dir

    def test_load_credentials_prefers_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_keyring: _MemoryKeyring,
    ) -> None:
        """.env / 环境变量应优先于加密凭证文件."""
        from umu_sdk.core import credential_loader as cl
        from umu_sdk.core import env_loader

        # 避免读取项目真实的 .env 文件
        monkeypatch.setattr(env_loader, "find_env_file", lambda _path=None: None)

        # 设置加密凭证
        cm.set_role_credentials("teacher", "encrypted_user", "encrypted_pass")

        # 设置环境变量
        monkeypatch.setenv("UMU_TEACHER_USERNAME", "env_user")
        monkeypatch.setenv("UMU_TEACHER_PASSWORD", "env_pass")

        username, password = cl.load_credentials("teacher")
        assert username == "env_user"
        assert password == "env_pass"

    def test_load_credentials_falls_back_to_encrypted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_keyring: _MemoryKeyring,
    ) -> None:
        from umu_sdk.core import credential_loader as cl
        from umu_sdk.core import env_loader

        monkeypatch.setattr(env_loader, "find_env_file", lambda _path=None: None)
        monkeypatch.delenv("UMU_TEACHER_USERNAME", raising=False)
        monkeypatch.delenv("UMU_TEACHER_PASSWORD", raising=False)

        cm.set_role_credentials("teacher", "encrypted_user", "encrypted_pass")

        username, password = cl.load_credentials("teacher")
        assert username == "encrypted_user"
        assert password == "encrypted_pass"


class TestCredentialLoaderSourceAndPriority:
    """测试 credential_loader 的凭证来源追踪与优先级."""

    @pytest.fixture(autouse=True)
    def _isolated_skill_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> Path:
        """通过环境变量隔离 credential_loader 使用的 skill 目录."""
        skill_dir = tmp_path / "umu"
        monkeypatch.setenv("UMU_SKILL_DIR", str(skill_dir))
        return skill_dir

    @pytest.fixture
    def dotenv_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """返回一个临时 .env 文件，并让 env_loader 指向它."""
        env_file = tmp_path / ".env"
        from umu_sdk.core import env_loader

        monkeypatch.setattr(env_loader, "find_env_file", lambda _path=None: env_file)
        return env_file

    def _write_dotenv(
        self, env_file: Path, role: str, username: str, password: str
    ) -> None:
        prefix = f"UMU_{role.upper()}"
        env_file.write_text(
            f'{prefix}_USERNAME="{username}"\n{prefix}_PASSWORD="{password}"\n',
            encoding="utf-8",
        )

    def test_explicit_param_beats_dotenv(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_keyring: _MemoryKeyring,
        dotenv_file: Path,
    ) -> None:
        """显式传入参数应优先于 .env."""
        from umu_sdk.core import credential_loader as cl

        self._write_dotenv(dotenv_file, "teacher", "dotenv_user", "dotenv_pass")
        username, password, source = cl.load_credentials_with_source(
            "teacher",
            explicit_username="explicit_user",
            explicit_password="explicit_pass",
        )
        assert username == "explicit_user"
        assert password == "explicit_pass"
        assert source == cl.CredentialSource.EXPLICIT

    def test_env_var_beats_dotenv(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_keyring: _MemoryKeyring,
        dotenv_file: Path,
    ) -> None:
        """环境变量应优先于 .env."""
        from umu_sdk.core import credential_loader as cl

        self._write_dotenv(dotenv_file, "teacher", "dotenv_user", "dotenv_pass")
        monkeypatch.setenv("UMU_TEACHER_USERNAME", "env_user")
        monkeypatch.setenv("UMU_TEACHER_PASSWORD", "env_pass")

        username, password, source = cl.load_credentials_with_source("teacher")
        assert username == "env_user"
        assert password == "env_pass"
        assert source == cl.CredentialSource.EXPLICIT

    def test_dotenv_beats_encrypted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_keyring: _MemoryKeyring,
        dotenv_file: Path,
    ) -> None:
        """.env 应优先于加密凭证."""
        from umu_sdk.core import credential_loader as cl

        self._write_dotenv(dotenv_file, "teacher", "dotenv_user", "dotenv_pass")
        monkeypatch.delenv("UMU_TEACHER_USERNAME", raising=False)
        monkeypatch.delenv("UMU_TEACHER_PASSWORD", raising=False)
        cm.set_role_credentials("teacher", "encrypted_user", "encrypted_pass")

        username, password, source = cl.load_credentials_with_source("teacher")
        assert username == "dotenv_user"
        assert password == "dotenv_pass"
        assert source == cl.CredentialSource.DOTENV

    def test_encrypted_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_keyring: _MemoryKeyring,
        dotenv_file: Path,
    ) -> None:
        """无显式参数/环境变量/.env 时回退到加密凭证."""
        from umu_sdk.core import credential_loader as cl

        dotenv_file.write_text("# no credentials\n", encoding="utf-8")
        monkeypatch.delenv("UMU_TEACHER_USERNAME", raising=False)
        monkeypatch.delenv("UMU_TEACHER_PASSWORD", raising=False)
        cm.set_role_credentials("teacher", "encrypted_user", "encrypted_pass")

        username, password, source = cl.load_credentials_with_source("teacher")
        assert username == "encrypted_user"
        assert password == "encrypted_pass"
        assert source == cl.CredentialSource.ENCRYPTED

    def test_no_credentials(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_keyring: _MemoryKeyring,
        dotenv_file: Path,
    ) -> None:
        """没有任何凭证时返回 NONE."""
        from umu_sdk.core import credential_loader as cl

        dotenv_file.write_text("# no credentials\n", encoding="utf-8")
        monkeypatch.delenv("UMU_TEACHER_USERNAME", raising=False)
        monkeypatch.delenv("UMU_TEACHER_PASSWORD", raising=False)

        username, password, source = cl.load_credentials_with_source("teacher")
        assert username is None
        assert password is None
        assert source == cl.CredentialSource.NONE

    def test_load_credentials_backwards_compatible(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_keyring: _MemoryKeyring,
        dotenv_file: Path,
    ) -> None:
        """旧签名仍返回二元组."""
        from umu_sdk.core import credential_loader as cl

        self._write_dotenv(dotenv_file, "teacher", "dotenv_user", "dotenv_pass")
        result = cl.load_credentials("teacher")
        assert result == ("dotenv_user", "dotenv_pass")

    def test_has_env_credentials(
        self,
        dotenv_file: Path,
    ) -> None:
        """has_env_credentials 正确判断 .env 中是否存在角色凭据."""
        from umu_sdk.core import env_loader

        assert not env_loader.has_env_credentials("teacher")
        self._write_dotenv(dotenv_file, "teacher", "u", "p")
        assert env_loader.has_env_credentials("teacher")
