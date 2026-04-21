from __future__ import annotations

import re
from enum import Enum
from typing import IO, Any

import httpx

from .auth import (
    ApiLoginRequestFactory,
    ApiTokenRequestFactory,
    CadSUSAuthenticator,
    CertTokenRequestFactory,
    default_api_login_request,
    default_api_token_request,
    default_cert_token_request,
)
from .cache import TokenCache, create_token_cache
from .config import CadSUSSettings
from .debug import ConsoleDebugTracer
from .exceptions import CadSUSRequestError
from .soap import SoapDocumentType, build_busca_pessoa_envelope, parse_busca_pessoa_response


class DocumentType(str, Enum):
    CPF = "CPF"
    CNS = "CNS"


class CadSUSClient:
    def __init__(
        self,
        settings: CadSUSSettings,
        *,
        cache: TokenCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        api_login_request_factory: ApiLoginRequestFactory | None = None,
        api_token_request_factory: ApiTokenRequestFactory | None = None,
        cert_token_request_factory: CertTokenRequestFactory | None = None,
    ) -> None:
        settings.validate()
        self._settings = settings
        self._transport = transport
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=settings.timeout,
            transport=transport,
        )
        self._cache = cache or create_token_cache(settings)
        self._authenticator = CadSUSAuthenticator(
            settings,
            self._cache,
            transport=transport,
            api_login_request_factory=api_login_request_factory
            or default_api_login_request,
            api_token_request_factory=api_token_request_factory
            or default_api_token_request,
            cert_token_request_factory=cert_token_request_factory
            or default_cert_token_request,
        )

    @classmethod
    def from_env(
        cls,
        *,
        cache: TokenCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        api_login_request_factory: ApiLoginRequestFactory | None = None,
        api_token_request_factory: ApiTokenRequestFactory | None = None,
        cert_token_request_factory: CertTokenRequestFactory | None = None,
    ) -> "CadSUSClient":
        settings = CadSUSSettings.from_env()
        return cls(
            settings,
            cache=cache,
            transport=transport,
            client=client,
            api_login_request_factory=api_login_request_factory,
            api_token_request_factory=api_token_request_factory,
            cert_token_request_factory=cert_token_request_factory,
        )

    async def __aenter__(self) -> "CadSUSClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def buscar_pessoa(self, identifier: str) -> dict[str, Any] | None:
        return await self._buscar_pessoa(identifier)

    async def buscar_pessoa_debug(
        self,
        identifier: str,
        *,
        stream: IO[str] | None = None,
        reveal_secrets: bool = False,
        include_response_body: bool = True,
    ) -> dict[str, Any] | None:
        debug = ConsoleDebugTracer(
            stream=stream,
            reveal_secrets=reveal_secrets,
            include_response_body=include_response_body,
        )
        debug.log_settings(self._settings)
        debug.log("Modo debug iniciado", identifier=identifier)
        try:
            result = await self._buscar_pessoa(identifier, debug=debug)
        except Exception as exc:
            debug.log_exception("Fluxo finalizado com erro", exc)
            raise

        debug.log("Fluxo finalizado com sucesso", result=result)
        return result

    async def _buscar_pessoa(
        self,
        identifier: str,
        *,
        debug: ConsoleDebugTracer | None = None,
    ) -> dict[str, Any] | None:
        normalized_identifier = normalize_identifier(identifier)
        if not normalized_identifier:
            if debug is not None:
                debug.log(
                    "Identificador informado nao possui digitos validos",
                    identifier=identifier,
                )
            raise CadSUSRequestError("O identificador informado esta vazio.")

        document_type = get_document_type(normalized_identifier)
        if debug is not None:
            debug.log(
                "Identificador normalizado para consulta",
                identifier_original=identifier,
                identifier_normalized=normalized_identifier,
                document_type=document_type.value,
            )

        token = await self._authenticator.get_token(debug=debug)
        envelope = build_busca_pessoa_envelope(
            normalized_identifier,
            SoapDocumentType(document_type.value),
            system_code=self._settings.system_code,
        )
        headers = {
            "Authorization": f"jwt {token}",
            "Content-Type": "application/soap+xml",
        }

        if debug is not None:
            debug.log_request(
                "Requisicao para API CADSUS",
                method="POST",
                url=self._settings.api_url,
                headers=headers,
                content=envelope.encode("utf-8"),
            )

        try:
            response = await self._client.post(
                self._settings.api_url,
                headers=headers,
                content=envelope.encode("utf-8"),
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if debug is not None:
                    debug.log_response("Resposta da API CADSUS com erro", exc.response)
                    debug.log_exception(
                        "Falha HTTP durante consulta ao CADSUS",
                        exc,
                        url=str(exc.request.url),
                    )
                raise CadSUSRequestError(
                    "Falha na consulta ao CADSUS.",
                    status_code=exc.response.status_code,
                    response_body=exc.response.text,
                ) from exc

            if debug is not None:
                debug.log_response("Resposta da API CADSUS", response)
        except httpx.HTTPError as exc:
            if debug is not None:
                debug.log_exception(
                    "Erro de comunicacao durante consulta ao CADSUS",
                    exc,
                    url=self._settings.api_url,
                )
            raise CadSUSRequestError(f"Erro de comunicacao com o CADSUS: {exc}") from exc

        try:
            result = parse_busca_pessoa_response(response.text)
        except Exception as exc:
            if debug is not None:
                debug.log_exception("Erro ao parsear resposta da API CADSUS", exc)
            raise
        if debug is not None:
            debug.log("Resposta da API CADSUS parseada", result=result)
        return result


async def buscar_pessoa(identifier: str) -> dict[str, Any] | None:
    async with CadSUSClient.from_env() as client:
        return await client.buscar_pessoa(identifier)


async def buscar_pessoa_debug(
    identifier: str,
    *,
    stream: IO[str] | None = None,
    reveal_secrets: bool = False,
    include_response_body: bool = True,
) -> dict[str, Any] | None:
    async with CadSUSClient.from_env() as client:
        return await client.buscar_pessoa_debug(
            identifier,
            stream=stream,
            reveal_secrets=reveal_secrets,
            include_response_body=include_response_body,
        )


def normalize_identifier(identifier: str) -> str:
    return re.sub(r"\D", "", identifier or "")


def get_document_type(identifier: str) -> DocumentType:
    return DocumentType.CPF if len(identifier) == 11 else DocumentType.CNS
