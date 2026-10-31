"""Encryption of stored secrets and masking of bot tokens."""

from __future__ import annotations

import base64
import hashlib
import re

from cryptography.fernet import Fernet, InvalidToken

TOKEN_RE = re.compile(r"(?<![0-9])\d{5,}:[A-Za-z0-9_-]{30,}(?![A-Za-z0-9_-])")
TOKEN_FORMAT = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")


def mask(text: str) -> str:
    """Replace anything that looks like a bot token with a placeholder."""
    return TOKEN_RE.sub("<token>", text)


def mask_value(value):
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, dict):
        return {k: mask_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_value(v) for v in value]
    return value


def short_token(token: str) -> str:
    """`123456:ABC...xyz` -> `123456:…xyz`, safe to show in the UI."""
    bot_id, _, rest = token.partition(":")
    return f"{bot_id}:…{rest[-4:]}"


class Cipher:
    """Fernet encryption with a key derived from SECRET_KEY.

    Changing SECRET_KEY makes stored tokens and secrets unreadable.
    """

    def __init__(self, secret_key: str):
        if len(secret_key) < 16:
            raise ValueError("SECRET_KEY must be at least 16 characters")
        key = base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken:
            raise ValueError("cannot decrypt value; was SECRET_KEY changed?") from None
