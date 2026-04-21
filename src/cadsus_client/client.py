from __future__ import annotations

import re
from enum import Enum
from typing import Any

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
        normalized_identifier = normalize_identifier(identifier)
        if not normalized_identifier:
            raise CadSUSRequestError("O identificador informado esta vazio.")

        document_type = get_document_type(normalized_identifier)
        token = await self._authenticator.get_token()
        envelope = build_busca_pessoa_envelope(
            normalized_identifier,
            SoapDocumentType(document_type.value),
            system_code=self._settings.system_code,
        )
        headers = {
            "Authorization": f"jwt {token}",
            "Content-Type": "application/soap+xml",
        }

        try:
            response = await self._client.post(
                self._settings.api_url,
                headers=headers,
                content=envelope.encode("utf-8"),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CadSUSRequestError(
                "Falha na consulta ao CADSUS.",
                status_code=exc.response.status_code,
                response_body=exc.response.text,
            ) from exc
        except httpx.HTTPError as exc:
            raise CadSUSRequestError(f"Erro de comunicacao com o CADSUS: {exc}") from exc

        return parse_busca_pessoa_response(response.text)


async def buscar_pessoa(identifier: str) -> dict[str, Any] | None:
    async with CadSUSClient.from_env() as client:
        return await client.buscar_pessoa(identifier)


def normalize_identifier(identifier: str) -> str:
    return re.sub(r"\D", "", identifier or "")


def get_document_type(identifier: str) -> DocumentType:
    return DocumentType.CPF if len(identifier) == 11 else DocumentType.CNS
