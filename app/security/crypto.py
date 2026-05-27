from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


ENC_PREFIX = "enc:v1:"


class SecretCryptoError(ValueError):
    pass


def is_encrypted_secret(value: str) -> bool:
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def _get_fernet() -> Fernet:
    secret = os.environ.get("GATEWAY_SECRET_KEY", "").strip()
    if not secret:
        raise SecretCryptoError("GATEWAY_SECRET_KEY is required to encrypt or decrypt secrets")
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    if not value:
        return value
    if is_encrypted_secret(value):
        return value
    token = _get_fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def decrypt_secret(value: str) -> str:
    if not is_encrypted_secret(value):
        return value
    token = value[len(ENC_PREFIX):]
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretCryptoError("Encrypted secret cannot be decrypted with current GATEWAY_SECRET_KEY") from exc
