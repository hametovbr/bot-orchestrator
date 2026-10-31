"""Minimal Bot API client."""

from __future__ import annotations

from typing import Any

import httpx

from .security import mask


class TelegramError(Exception):
    """Bot API answered with ok=false (code is the error_code) or could not be reached."""

    def __init__(self, method: str, code: int | None, description: str):
        super().__init__(f"{method}: {description}")
        self.method = method
        self.code = code
        self.description = description

    @property
    def unreachable(self) -> bool:
        return self.code is None


class BotApi:
    def __init__(self, http: httpx.AsyncClient, base_url: str, token: str):
        self._http = http
        self._url = f"{base_url.rstrip('/')}/bot{token}/"

    async def raw(
        self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        """Call a method and return the whole JSON response (ok, result, description...)."""
        try:
            response = await self._http.post(
                self._url + method,
                json=params or {},
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.HTTPError as exc:
            raise TelegramError(method, None, mask(f"{type(exc).__name__}: {exc}")) from None
        try:
            return response.json()
        except ValueError:
            raise TelegramError(
                method, None, f"HTTP {response.status_code}: not a JSON response"
            ) from None

    async def call(
        self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> Any:
        data = await self.raw(method, params, timeout)
        if not data.get("ok"):
            raise TelegramError(
                method, data.get("error_code", 0), data.get("description", "unknown error")
            )
        return data.get("result")
