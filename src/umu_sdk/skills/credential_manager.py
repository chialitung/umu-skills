# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""UMU Skills 凭证管理器.

使用 Fernet 对称加密保存账号信息，并用操作系统 keyring（Windows DPAPI /
macOS Keychain / Linux Secret Service）保护 Fernet 密钥。

开发阶段：凭证文件优先放在项目根目录的 `.claude/skills/umu/credentials.enc`。
发布阶段：凭证文件放在用户 Claude Code 全局 skills 目录的 `umu/credentials.enc`。

如果 keyring 不可用，会回退到把密钥保存在同目录的 `.key` 文件中（安全性降低，
但仍优于明文存储）。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("umu.credentials")

KEYRING_SERVICE = "umu-skills"
KEYRING_USERNAME = "fernet-key"
CREDENTIALS_FILENAME = "credentials.enc"
FALLBACK_KEY_FILENAME = ".fernet.key"


def get_skill_dir() -> Path:
    """定位 skill 目录.

    优先级：
    1. 环境变量 UMU_SKILL_DIR
    2. 项目开发目录（项目根目录/.claude/skills/umu）
    3. 用户全局 Claude Code skills 目录（~/.claude/skills/umu）
    """
    if env_dir := os.getenv("UMU_SKILL_DIR"):
        return Path(env_dir)

    # 开发阶段：项目根目录
    project_root = Path(__file__).resolve().parents[3]
    dev_dir = project_root / ".claude" / "skills" / "umu"
    if dev_dir.exists():
        return dev_dir

    # 发布阶段：用户全局 skills 目录
    return Path.home() / ".claude" / "skills" / "umu"


def _keyring_available() -> bool:
    """检查 keyring 是否可用."""
    try:
        import keyring  # noqa: F401

        return True
    except ImportError:
        return False


def _get_keyring_password() -> str | None:
    """从 keyring 读取 Fernet 密钥."""
    if not _keyring_available():
        return None
    import keyring

    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception as e:  # noqa: BLE001
        logger.debug("无法从 keyring 读取密钥: %s", e)
        return None


def _set_keyring_password(key: str) -> bool:
    """把 Fernet 密钥写入 keyring."""
    if not _keyring_available():
        return False
    import keyring

    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("无法写入 keyring: %s", e)
        return False


def _fallback_key_path(skill_dir: Path) -> Path:
    return skill_dir / FALLBACK_KEY_FILENAME


def _load_or_create_key(skill_dir: Path) -> bytes:
    """加载或创建 Fernet 密钥.

    优先使用 keyring，不可用时回退到同目录下的 .fernet.key 文件。
    """
    existing = _get_keyring_password()
    if existing:
        return existing.encode()

    # 检查 fallback 密钥文件
    fallback_path = _fallback_key_path(skill_dir)
    if fallback_path.exists():
        key = fallback_path.read_text(encoding="utf-8").strip()
        # 尝试迁移到 keyring
        if _set_keyring_password(key):
            fallback_path.unlink(missing_ok=True)
        return key.encode()

    # 创建新密钥
    key = Fernet.generate_key().decode()
    if _set_keyring_password(key):
        logger.info("Fernet 密钥已保存到系统 keyring")
    else:
        skill_dir.mkdir(parents=True, exist_ok=True)
        fallback_path.write_text(key, encoding="utf-8")
        logger.warning(
            "keyring 不可用，Fernet 密钥已保存到 %s。"
            "建议安装 keyring 后端以获得更好的安全性。",
            fallback_path,
        )

    return key.encode()


def _get_fernet(skill_dir: Path | None = None) -> Fernet:
    skill_dir = skill_dir or get_skill_dir()
    return Fernet(_load_or_create_key(skill_dir))


def get_credentials_path(skill_dir: Path | None = None) -> Path:
    """返回加密凭证文件路径."""
    return (skill_dir or get_skill_dir()) / CREDENTIALS_FILENAME


def load_credentials(skill_dir: Path | None = None) -> dict[str, dict[str, str]]:
    """加载并解密 skills 目录下的凭证文件.

    返回结构：
        {
            "teacher": {"username": "...", "password": "..."},
            "student": {"username": "...", "password": "..."},
            "admin": {"username": "...", "password": "..."}
        }
    """
    path = get_credentials_path(skill_dir)
    if not path.exists():
        return {}

    try:
        encrypted = path.read_bytes()
        fernet = _get_fernet(skill_dir)
        decrypted = fernet.decrypt(encrypted)
        data = json.loads(decrypted.decode("utf-8"))
    except (InvalidToken, json.JSONDecodeError, OSError) as e:
        logger.warning("读取加密凭证失败: %s", e)
        return {}

    if not isinstance(data, dict):
        return {}
    return data


def save_credentials(
    credentials: dict[str, dict[str, str]],
    skill_dir: Path | None = None,
) -> None:
    """加密并保存凭证到 skills 目录."""
    skill_dir = skill_dir or get_skill_dir()
    skill_dir.mkdir(parents=True, exist_ok=True)

    fernet = _get_fernet(skill_dir)
    encrypted = fernet.encrypt(json.dumps(credentials).encode("utf-8"))
    get_credentials_path(skill_dir).write_bytes(encrypted)


def get_role_credentials(
    role: Literal["admin", "teacher", "student"],
    skill_dir: Path | None = None,
) -> tuple[str | None, str | None]:
    """获取指定角色的用户名和密码."""
    creds = load_credentials(skill_dir)
    role_creds = creds.get(role, {})
    return role_creds.get("username"), role_creds.get("password")


def set_role_credentials(
    role: Literal["admin", "teacher", "student"],
    username: str,
    password: str,
    skill_dir: Path | None = None,
) -> None:
    """设置指定角色的用户名和密码."""
    creds = load_credentials(skill_dir)
    creds[role] = {"username": username, "password": password}
    save_credentials(creds, skill_dir)


def has_role_credentials(
    role: Literal["admin", "teacher", "student"],
    skill_dir: Path | None = None,
) -> bool:
    """检查指定角色是否已配置凭证."""
    username, password = get_role_credentials(role, skill_dir)
    return bool(username and password)


def list_configured_roles(skill_dir: Path | None = None) -> list[str]:
    """返回已配置凭证的所有角色列表."""
    creds = load_credentials(skill_dir)
    return [
        role
        for role, values in creds.items()
        if isinstance(values, dict) and values.get("username") and values.get("password")
    ]


def delete_role_credentials(
    role: Literal["admin", "teacher", "student"],
    skill_dir: Path | None = None,
) -> None:
    """删除指定角色的凭证."""
    creds = load_credentials(skill_dir)
    if role in creds:
        del creds[role]
        save_credentials(creds, skill_dir)


__all__ = [
    "get_skill_dir",
    "get_credentials_path",
    "load_credentials",
    "save_credentials",
    "get_role_credentials",
    "set_role_credentials",
    "has_role_credentials",
    "list_configured_roles",
    "delete_role_credentials",
]
