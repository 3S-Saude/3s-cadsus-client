from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import httpx

from .cache import CachedToken, TokenCache
from .config import AuthMethod, CadSUSSettings
from .exceptions import CadSUSAuthenticationError


@dataclass(frozen=True, slots=True)
class RequestDefinition:
    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    params: Mapping[str, Any] | None = None
    json: Any | None = None
    data: Any | None = None
    content: bytes | str | None = None


ApiLoginRequestFactory = Callable[[CadSUSSettings], RequestDefinition]
ApiTokenRequestFactory = Callable[[CadSUSSettings, str], RequestDefinition]
CertTokenRequestFactory = Callable[[CadSUSSettings], RequestDefinition]


def default_api_login_request(settings: CadSUSSettings) -> RequestDefinition:
    return RequestDefinition(
        method="POST",
        url=settings.auth_login_url or "",
        json={"username": settings.user, "password": settings.password},
    )


def default_api_token_request(
    settings: CadSUSSettings,
    login_token: str,
) -> RequestDefinition:
    return RequestDefinition(
        method="POST",
        url=settings.auth_token_url,
        headers={"Authorization": f"Bearer {login_token}"},
    )


def default_cert_token_request(settings: CadSUSSettings) -> RequestDefinition:
    return RequestDefinition(method="POST", url=settings.auth_token_url)


class CadSUSAuthenticator:
    def __init__(
        self,
        settings: CadSUSSettings,
        cache: TokenCache,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        api_login_request_factory: ApiLoginRequestFactory = default_api_login_request,
        api_token_request_factory: ApiTokenRequestFactory = default_api_token_request,
        cert_token_request_factory: CertTokenRequestFactory = default_cert_token_request,
    ) -> None:
        self._settings = settings
        self._cache = cache
        self._transport = transport
        self._api_login_request_factory = api_login_request_factory
        self._api_token_request_factory = api_token_request_factory
        self._cert_token_request_factory = cert_token_request_factory
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        cached = await self._cache.get(self._settings.cache_key)
        if cached is not None:
            return cached.value

        async with self._lock:
            cached = await self._cache.get(self._settings.cache_key)
            if cached is not None:
                return cached.value

            token = await self._authenticate()
            await self._cache.set(self._settings.cache_key, token)
            return token.value

    async def _authenticate(self) -> CachedToken:
        if self._settings.auth_method is AuthMethod.API:
            return await self._authenticate_via_api()
        return await self._authenticate_via_cert()

    async def _authenticate_via_api(self) -> CachedToken:
        login_response = await self._execute(
            self._api_login_request_factory(self._settings)
        )
        login_payload = _safe_json(login_response)
        login_token = _extract_access_token(login_payload, login_response.text)
        if not login_token:
            raise CadSUSAuthenticationError(
                "Nao foi possivel extrair o access_token da resposta de login."
            )

        token_response = await self._execute(
            self._api_token_request_factory(self._settings, login_token)
        )
        token_payload = _safe_json(token_response)
        access_token = _extract_access_token(token_payload, token_response.text)
        if not access_token:
            raise CadSUSAuthenticationError(
                "Nao foi possivel extrair o access_token final do CADSUS."
            )

        return CachedToken(
            value=access_token,
            expires_at=_resolve_expiration(
                token_payload,
                access_token,
                fallback_seconds=self._settings.token_ttl_fallback,
            ),
        )

    async def _authenticate_via_cert(self) -> CachedToken:
        response = await self._execute(
            self._cert_token_request_factory(self._settings),
            cert=(self._settings.cert, self._settings.key),
        )
        payload = _safe_json(response)
        access_token = _extract_access_token(payload, response.text)
        if not access_token:
            raise CadSUSAuthenticationError(
                "Nao foi possivel extrair o access_token do fluxo por certificado."
            )

        return CachedToken(
            value=access_token,
            expires_at=_resolve_expiration(
                payload,
                access_token,
                fallback_seconds=self._settings.token_ttl_fallback,
            ),
        )

    async def _execute(
        self,
        request_definition: RequestDefinition,
        *,
        cert: tuple[str | None, str | None] | None = None,
    ) -> httpx.Response:
        client_kwargs: dict[str, Any] = {
            "timeout": self._settings.timeout,
            "transport": self._transport,
        }
        if cert and cert[0] and cert[1]:
            client_kwargs["cert"] = cert

        async with httpx.AsyncClient(**client_kwargs) as client:
            try:
                response = await client.request(
                    request_definition.method,
                    request_definition.url,
                    headers=dict(request_definition.headers),
                    params=request_definition.params,
                    json=request_definition.json,
                    data=request_definition.data,
                    content=request_definition.content,
                )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                raise CadSUSAuthenticationError(
                    f"Falha de autenticacao no endpoint {request_definition.url}: "
                    f"{exc.response.status_code}"
                ) from exc
            except httpx.HTTPError as exc:
                raise CadSUSAuthenticationError(
                    f"Erro de comunicacao durante a autenticacao: {exc}"
                ) from exc


def _safe_json(response: httpx.Response) -> Any | None:
    try:
        return response.json()
    except ValueError:
        return None


def _extract_access_token(payload: Any | None, raw_text: str | None = None) -> str | None:
    for key in ("access_token", "token", "jwt"):
        value = _find_nested_value(payload, key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if raw_text and raw_text.strip() and " " not in raw_text.strip():
        return raw_text.strip()
    return None


def _find_nested_value(payload: Any | None, target_key: str) -> Any | None:
    if isinstance(payload, dict):
        if target_key in payload:
            return payload[target_key]
        for value in payload.values():
            found = _find_nested_value(value, target_key)
            if found is not None:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _find_nested_value(item, target_key)
            if found is not None:
                return found
    return None


def _resolve_expiration(
    payload: Any | None,
    token: str,
    *,
    fallback_seconds: int,
) -> float:
    now = time.time()
    expires_in = _find_nested_value(payload, "expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        return max(now + 30, now + float(expires_in) - 60)

    jwt_exp = _extract_jwt_exp(token)
    if jwt_exp is not None:
        return max(now + 30, jwt_exp - 60)

    return now + fallback_seconds


def _extract_jwt_exp(token: str) -> float | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None

    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        data = json.loads(decoded)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None

    exp = data.get("exp")
    if isinstance(exp, (int, float)):
        return float(exp)
    return None

