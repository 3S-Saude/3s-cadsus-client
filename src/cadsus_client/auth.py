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
from .debug import ConsoleDebugTracer
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

    async def get_token(self, *, debug: ConsoleDebugTracer | None = None) -> str:
        if debug is not None:
            debug.log(
                "Verificando cache de token antes da autenticacao",
                cache_key=self._settings.cache_key,
            )
        cached = await self._cache.get(self._settings.cache_key)
        if cached is not None:
            if debug is not None:
                debug.log_cache_hit(self._settings.cache_key, cached)
            return cached.value

        async with self._lock:
            cached = await self._cache.get(self._settings.cache_key)
            if cached is not None:
                if debug is not None:
                    debug.log_cache_hit(self._settings.cache_key, cached)
                return cached.value
            if debug is not None:
                debug.log_cache_miss(self._settings.cache_key)

            token = await self._authenticate(debug=debug)
            await self._cache.set(self._settings.cache_key, token)
            if debug is not None:
                debug.log(
                    "Token salvo no cache",
                    cache_key=self._settings.cache_key,
                    expires_at=token.expires_at,
                    expires_at_iso=_format_timestamp(token.expires_at),
                )
            return token.value

    async def _authenticate(
        self,
        *,
        debug: ConsoleDebugTracer | None = None,
    ) -> CachedToken:
        if self._settings.auth_method is AuthMethod.API:
            if debug is not None:
                debug.log("Fluxo de autenticacao selecionado", auth_method="API")
            return await self._authenticate_via_api(debug=debug)
        if debug is not None:
            debug.log("Fluxo de autenticacao selecionado", auth_method="CERT")
        return await self._authenticate_via_cert(debug=debug)

    async def _authenticate_via_api(
        self,
        *,
        debug: ConsoleDebugTracer | None = None,
    ) -> CachedToken:
        login_request = self._api_login_request_factory(self._settings)
        login_response = await self._execute(
            login_request,
            debug=debug,
            request_label="Requisicao de login",
        )
        login_payload = _safe_json(login_response)
        login_token = _extract_access_token(login_payload, login_response.text)
        if not login_token:
            raise CadSUSAuthenticationError(
                "Nao foi possivel extrair o access_token da resposta de login."
            )
        if debug is not None:
            debug.log("Token de login extraido", login_token=login_token)

        token_request = self._api_token_request_factory(self._settings, login_token)
        token_response = await self._execute(
            token_request,
            debug=debug,
            request_label="Requisicao ao CADSUS_AUTH_TOKEN_URL",
        )
        token_payload = _safe_json(token_response)
        access_token = _extract_access_token(token_payload, token_response.text)
        if not access_token:
            raise CadSUSAuthenticationError(
                "Nao foi possivel extrair o access_token final do CADSUS."
            )
        if debug is not None:
            debug.log("Token final do CADSUS extraido", access_token=access_token)

        expires_at = _resolve_expiration(
            access_token,
            fallback_seconds=self._settings.token_ttl_fallback,
            debug=debug,
        )

        return CachedToken(
            value=access_token,
            expires_at=expires_at,
        )

    async def _authenticate_via_cert(
        self,
        *,
        debug: ConsoleDebugTracer | None = None,
    ) -> CachedToken:
        request = self._cert_token_request_factory(self._settings)
        response = await self._execute(
            request,
            cert=(self._settings.cert, self._settings.key),
            debug=debug,
            request_label="Requisicao ao CADSUS_AUTH_TOKEN_URL com certificado",
        )
        payload = _safe_json(response)
        access_token = _extract_access_token(payload, response.text)
        if not access_token:
            raise CadSUSAuthenticationError(
                "Nao foi possivel extrair o access_token do fluxo por certificado."
            )
        if debug is not None:
            debug.log("Token do fluxo com certificado extraido", access_token=access_token)

        expires_at = _resolve_expiration(
            access_token,
            fallback_seconds=self._settings.token_ttl_fallback,
            debug=debug,
        )

        return CachedToken(
            value=access_token,
            expires_at=expires_at,
        )

    async def _execute(
        self,
        request_definition: RequestDefinition,
        *,
        cert: tuple[str | None, str | None] | None = None,
        debug: ConsoleDebugTracer | None = None,
        request_label: str = "Requisicao de autenticacao",
    ) -> httpx.Response:
        if debug is not None:
            debug.log_request(
                request_label,
                method=request_definition.method,
                url=request_definition.url,
                headers=request_definition.headers,
                params=request_definition.params,
                json_body=request_definition.json,
                data=request_definition.data,
                content=request_definition.content,
                cert=cert,
            )

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
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    if debug is not None:
                        debug.log_response(
                            f"{request_label} - resposta com erro",
                            exc.response,
                        )
                        debug.log_exception(
                            "Falha HTTP durante autenticacao",
                            exc,
                            url=request_definition.url,
                        )
                    raise CadSUSAuthenticationError(
                        f"Falha de autenticacao no endpoint {request_definition.url}: "
                        f"{exc.response.status_code}"
                    ) from exc

                if debug is not None:
                    debug.log_response(f"{request_label} - resposta", response)

                return response
            except httpx.HTTPError as exc:
                if debug is not None:
                    debug.log_exception(
                        "Erro de comunicacao durante autenticacao",
                        exc,
                        url=request_definition.url,
                    )
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
    token: str,
    *,
    fallback_seconds: int,
    debug: ConsoleDebugTracer | None = None,
) -> float:
    now = time.time()
    jwt_exp = _extract_jwt_exp(token)
    if jwt_exp is not None:
        if debug is not None:
            debug.log(
                "Expiracao resolvida pela claim exp do JWT",
                expires_at=jwt_exp,
                expires_at_iso=_format_timestamp(jwt_exp),
            )
        return jwt_exp

    expires_at = now + fallback_seconds
    if debug is not None:
        debug.log(
            "Claim exp ausente ou invalida; usando fallback de expiracao",
            fallback_seconds=fallback_seconds,
            expires_at=expires_at,
            expires_at_iso=_format_timestamp(expires_at),
        )
    return expires_at


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


def _format_timestamp(value: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(value))
