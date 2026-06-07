"""UMU 密码加密模块.

基于逆向分析结果：
- 算法: AES-256-CBC
- 密钥: muumuumuumuumuumuumuumumumuumuum (UTF-8, 32字节)
- IV: mumumuumumumumum (UTF-8, 16字节)
- Padding: PKCS7
- 输出: Base64

对应 TypeScript 版本的 CryptoJS 实现.
"""

import base64

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.hazmat.backends import default_backend

# 硬编码密钥和 IV（从 umu-util webpack 模块逆向提取）
_AES_KEY = b"muumuumuumuumuumuumuumumumuumuum"
_AES_IV = b"mumumuumumumumum"


def encrypt_password(password: str) -> str:
    """使用 AES-256-CBC 加密密码.

    Args:
        password: 明文密码

    Returns:
        Base64 编码的密文

    Example:
        >>> encrypt_password("TestPassword123!")
        'WIEvF2mrRcJkBW3Yg4aS12F4HZLK/Tyo5+71mqm8Ohg='
    """
    padder = PKCS7(algorithms.AES.block_size).padder()
    padded_data = padder.update(password.encode("utf-8")) + padder.finalize()

    cipher = Cipher(
        algorithms.AES(_AES_KEY),
        modes.CBC(_AES_IV),
        backend=default_backend(),
    )
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded_data) + encryptor.finalize()

    return base64.b64encode(ciphertext).decode("ascii")


def decrypt_password(encrypted_base64: str) -> str:
    """使用 AES-256-CBC 解密密码.

    Args:
        encrypted_base64: Base64 编码的密文

    Returns:
        明文密码
    """
    ciphertext = base64.b64decode(encrypted_base64)

    cipher = Cipher(
        algorithms.AES(_AES_KEY),
        modes.CBC(_AES_IV),
        backend=default_backend(),
    )
    decryptor = cipher.decryptor()
    padded_data = decryptor.update(ciphertext) + decryptor.finalize()

    unpadder = PKCS7(algorithms.AES.block_size).unpadder()
    data = unpadder.update(padded_data) + unpadder.finalize()

    return data.decode("utf-8")


def verify_encryption() -> bool:
    """验证加密/解密实现是否正确.

    Returns:
        True 如果验证通过
    """
    test_password = "TestPassword123!"
    expected = "WIEvF2mrRcJkBW3Yg4aS12F4HZLK/Tyo5+71mqm8Ohg="

    encrypted = encrypt_password(test_password)
    decrypted = decrypt_password(encrypted)

    return encrypted == expected and decrypted == test_password
