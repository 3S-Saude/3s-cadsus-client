from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any, Mapping, TextIO

import httpx

from .cache import CachedToken
from .config import CadSUSSettings


class ConsoleDebugTracer:
    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        reveal_secrets: bool = False,
        include_response_body: bool = True,
    ) -> None:
        self._stream = stream or sys.stdout
        self._reveal_secrets = reveal_secrets
        self._include_response_body = include_response_body

    def log(self, message: str, **fields: Any) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        print(f"[CADSUS DEBUG {timestamp}] {message}", file=self._stream)
        for key, value in fields.items():
            rendered = self._render_value(key, value)
            if "\n" not in rendered:
                print(f"  - {key}: {rendered}", file=self._stream)
                continue

            print(f"  - {key}:", file=self._stream)
            for line in rendered.splitlines():
                print(f"      {line}", file=self._stream)
        self._stream.flush()

    def log_settings(self, settings: CadSUSSettings) -> None:
        self.log(
            "Variaveis resolvidas para o fluxo CADSUS",
            **_build_environment_snapshot(settings),
        )

    def log_cache_hit(self, key: str, token: CachedToken) -> None:
        self.log(
            "Token encontrado em cache",
            cache_key=key,
            expires_at=token.expires_at,
            expires_at_iso=_format_timestamp(token.expires_at),
            seconds_remaining=max(0, int(token.expires_at - _current_timestamp())),
            token=token.value,
        )

    def log_cache_miss(self, key: str) -> None:
        self.log("Token nao encontrado em cache", cache_key=key)

    def log_request(
        self,
        message: str,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        data: Any | None = None,
        content: bytes | str | None = None,
        cert: tuple[str | None, str | None] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"method": method, "url": url}
        if headers:
            payload["headers"] = dict(headers)
        if params:
            payload["params"] = dict(params)
        if json_body is not None:
            payload["json"] = json_body
        if data is not None:
            payload["data"] = data
        if content is not None:
            payload["content"] = content
        if cert is not None:
            payload["cert"] = {"cert": cert[0], "key": cert[1]}

        self.log(message, **payload)

    def log_response(self, message: str, response: httpx.Response) -> None:
        payload: dict[str, Any] = {
            "status_code": response.status_code,
            "headers": dict(response.headers),
        }
        if self._include_response_body:
            payload["body"] = response.text
        self.log(message, **payload)

    def log_exception(self, message: str, exc: Exception, **fields: Any) -> None:
        self.log(
            message,
            exception_type=type(exc).__name__,
            exception_message=str(exc),
            **fields,
        )

    def _render_value(self, key: str, value: Any) -> str:
        sanitized = self._sanitize(value, key)
        if isinstance(sanitized, str):
            return sanitized
        if sanitized is None:
            return "None"
        return json.dumps(
            sanitized,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )

    def _sanitize(self, value: Any, key: str | None = None) -> Any:
        if isinstance(value, httpx.URL):
            return str(value)
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, Mapping):
            return {
                str(item_key): self._sanitize(item_value, str(item_key))
                for item_key, item_value in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [self._sanitize(item, key) for item in value]
        if isinstance(value, str):
            if self._reveal_secrets:
                return value
            return _mask_string(value, key)
        return value


def _build_environment_snapshot(settings: CadSUSSettings) -> dict[str, Any]:
    return {
        "CADSUS_AUTH_METHOD": settings.auth_method.value,
        "CADSUS_AUTH_LOGIN_URL": settings.auth_login_url,
        "CADSUS_AUTH_TOKEN_URL": settings.auth_token_url,
        "CADSUS_API_URL": settings.api_url,
        "CADSUS_USER": settings.user,
        "CADSUS_PASSWORD": settings.password,
        "CADSUS_CERT": settings.cert,
        "CADSUS_KEY": settings.key,
        "CADSUS_SYSTEM_CODE": settings.system_code,
        "CADSUS_TIMEOUT": settings.timeout,
        "CADSUS_CACHE_ALIAS": settings.cache_alias,
        "CADSUS_TOKEN_TTL_FALLBACK": settings.token_ttl_fallback,
        "CADSUS_CACHE_KEY": settings.cache_key,
    }


def _mask_string(value: str, key: str | None) -> str:
    if not value:
        return value

    normalized_key = (key or "").strip().lower()
    if normalized_key in {"cadsus_password", "password"}:
        return "***"
    if normalized_key == "authorization":
        return _mask_authorization(value)
    if normalized_key in {"token", "access_token", "login_token", "jwt"}:
        return _mask_token(value)
    if _looks_like_jwt(value):
        return _mask_token(value)
    return value


def _mask_authorization(value: str) -> str:
    parts = value.split(" ", 1)
    if len(parts) != 2:
        return _mask_token(value)
    return f"{parts[0]} {_mask_token(parts[1])}"


def _mask_token(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _looks_like_jwt(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 3 and all(parts)


def _current_timestamp() -> float:
    return datetime.now().timestamp()


def _format_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value).astimezone().isoformat(timespec="seconds")
