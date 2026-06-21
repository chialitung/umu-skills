# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""UMU Skills 凭证管理器.

使用 Fernet 对称加密保存账号信息，并用操作系统 keyring（Windows DPAPI /
macOS Keychain / Linux Secret Service）保护 Fernet 密钥。

凭证文件默认保存在独立的 `~/.umu_skills/credentials.enc`，不依赖任何特定 AI
客户端的目录结构，以便 Claude Code、WorkBuddy 等工具共享同一套加密凭证。

为了兼容旧版本，读取时仍会回退到旧的 `~/.claude/skills/umu/credentials.enc`；
写入新路径时会自动迁移并删除旧文件。

如果 keyring 不可用，会回退到把密钥保存在同目录的 `.fernet.key` 文件中（安全性降低，
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

# 新版默认凭证目录（独立于 AI 客户端）
_NEW_GLOBAL_SKILL_DIR = Path.home() / ".umu_skills"
# 旧版默认凭证目录（Claude Code 专用），保留用于读取兼容与迁移
_OLD_GLOBAL_SKILL_DIR = Path.home() / ".claude" / "skills" / "umu"


def get_skill_dir() -> Path:
    """定位 skill 目录.

    优先级：
    1. 环境变量 UMU_SKILL_DIR
    2. 项目开发目录（项目根目录/.umu_skills）
    3. 旧项目开发目录（项目根目录/.claude/skills/umu）—— 向后兼容
    4. 用户全局目录（~/.umu_skills）
    """
    if env_dir := os.getenv("UMU_SKILL_DIR"):
        return Path(env_dir)

    # 开发阶段：项目根目录（新路径）
    project_root = Path(__file__).resolve().parents[3]
    dev_dir = project_root / ".umu_skills"
    if dev_dir.exists():
        return dev_dir

    # 向后兼容：旧开发路径
    old_dev_dir = project_root / ".claude" / "skills" / "umu"
    if old_dev_dir.exists():
        return old_dev_dir

    # 发布阶段：用户全局目录（新路径）
    return _NEW_GLOBAL_SKILL_DIR


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
    # 向后兼容：新路径不存在时尝试旧全局路径
    if not path.exists():
        old_path = _OLD_GLOBAL_SKILL_DIR / CREDENTIALS_FILENAME
        if old_path.exists():
            path = old_path
        else:
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


def _migrate_old_credentials_if_needed(skill_dir: Path | None = None) -> Path | None:
    """若旧全局路径存在凭证而新路径没有，则复制旧文件到新路径。

    仅在未显式传入 skill_dir 且使用新版全局目录时执行，避免误操作测试目录。
    返回迁移的源旧文件路径（若执行了迁移），否则返回 None。
    """
    if skill_dir is not None:
        return None

    current_dir = get_skill_dir().resolve()
    if current_dir != _NEW_GLOBAL_SKILL_DIR.resolve():
        return None

    old_creds = _OLD_GLOBAL_SKILL_DIR / CREDENTIALS_FILENAME
    new_creds = current_dir / CREDENTIALS_FILENAME
    if old_creds.exists() and not new_creds.exists():
        current_dir.mkdir(parents=True, exist_ok=True)
        new_creds.write_bytes(old_creds.read_bytes())
        logger.info("已从旧路径迁移凭证: %s -> %s", old_creds, new_creds)
        return old_creds
    return None


def save_credentials(
    credentials: dict[str, dict[str, str]],
    skill_dir: Path | None = None,
) -> None:
    """加密并保存凭证到 skills 目录."""
    # 仅在调用者未显式指定 skill_dir 时才尝试迁移旧全局凭证
    migrated_old_creds = _migrate_old_credentials_if_needed(skill_dir)

    skill_dir = skill_dir or get_skill_dir()
    skill_dir.mkdir(parents=True, exist_ok=True)

    fernet = _get_fernet(skill_dir)
    encrypted = fernet.encrypt(json.dumps(credentials).encode("utf-8"))
    get_credentials_path(skill_dir).write_bytes(encrypted)

    # 迁移成功后清理旧路径凭证文件与 fallback 密钥文件
    if migrated_old_creds is not None:
        try:
            migrated_old_creds.unlink(missing_ok=True)
            (_OLD_GLOBAL_SKILL_DIR / FALLBACK_KEY_FILENAME).unlink(missing_ok=True)
            logger.info("已删除旧路径凭证文件: %s", _OLD_GLOBAL_SKILL_DIR)
        except OSError as e:
            logger.warning("清理旧路径凭证文件失败: %s", e)


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
