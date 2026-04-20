from __future__ import annotations

import base64
import json
import time
import unittest

import httpx

from three_s_cadsus_client import CadSUSClient, CadSUSSettings
from three_s_cadsus_client.client import DocumentType, get_document_type, normalize_identifier
from three_s_cadsus_client.config import AuthMethod
from three_s_cadsus_client.soap import SoapDocumentType, build_busca_pessoa_envelope


def build_fake_jwt(expiration: int | None = None) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    payload = {}
    if expiration is not None:
        payload["exp"] = expiration
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{body}.signature"


class CadSUSClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_buscar_pessoa_uses_cached_token(self) -> None:
        counters = {"login": 0, "token": 0, "api": 0}
        jwt_token = build_fake_jwt(int(time.time()) + 3600)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url == httpx.URL("https://example.test/login"):
                counters["login"] += 1
                return httpx.Response(200, json={"access_token": "login-token"})

            if request.url == httpx.URL("https://example.test/token"):
                counters["token"] += 1
                self.assertEqual(request.headers.get("Authorization"), "Bearer login-token")
                return httpx.Response(200, json={"access_token": jwt_token})

            if request.url == httpx.URL("https://example.test/api"):
                counters["api"] += 1
                self.assertEqual(request.headers.get("Authorization"), f"jwt {jwt_token}")
                return httpx.Response(200, text="<ok/>")

            self.fail(f"Unexpected request URL: {request.url}")

        settings = CadSUSSettings(
            auth_method=AuthMethod.API,
            auth_login_url="https://example.test/login",
            auth_token_url="https://example.test/token",
            api_url="https://example.test/api",
            user="user",
            password="password",
        )

        async with CadSUSClient(
            settings,
            transport=httpx.MockTransport(handler),
        ) as client:
            await client.buscar_pessoa("123.456.789-01")
            await client.buscar_pessoa("12345678901")

        self.assertEqual(counters, {"login": 1, "token": 1, "api": 2})

    async def test_buscar_pessoa_identifies_cns(self) -> None:
        counters = {"token": 0}
        jwt_token = build_fake_jwt(int(time.time()) + 3600)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url == httpx.URL("https://example.test/token"):
                counters["token"] += 1
                return httpx.Response(200, json={"access_token": jwt_token})
            if request.url == httpx.URL("https://example.test/api"):
                content = request.content.decode()
                self.assertIn('root="2.16.840.1.113883.13.236"', content)
                self.assertIn('extension="898001160366001"', content)
                return httpx.Response(200, text="<ok/>")
            self.fail(f"Unexpected request URL: {request.url}")

        settings = CadSUSSettings(
            auth_method=AuthMethod.CERT,
            auth_token_url="https://example.test/token",
            api_url="https://example.test/api",
            cert="/tmp/cert.pem",
            key="/tmp/key.pem",
        )

        async with CadSUSClient(
            settings,
            transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.buscar_pessoa("898001160366001")

        self.assertEqual(result.document_type, DocumentType.CNS)
        self.assertEqual(counters["token"], 1)


class IdentifierTests(unittest.TestCase):
    def test_normalize_identifier_removes_non_digits(self) -> None:
        self.assertEqual(normalize_identifier("123.456.789-01 "), "12345678901")

    def test_get_document_type_uses_length(self) -> None:
        self.assertEqual(get_document_type("12345678901"), DocumentType.CPF)
        self.assertEqual(get_document_type("898001160366001"), DocumentType.CNS)

    def test_build_busca_pessoa_envelope_uses_correct_root(self) -> None:
        cpf_envelope = build_busca_pessoa_envelope(
            "12345678901",
            SoapDocumentType.CPF,
            system_code="MY-SYSTEM",
        )
        cns_envelope = build_busca_pessoa_envelope(
            "898001160366001",
            SoapDocumentType.CNS,
            system_code="CADSUS",
        )

        self.assertIn('root="2.16.840.1.113883.13.237"', cpf_envelope)
        self.assertIn('extension="12345678901"', cpf_envelope)
        self.assertIn("<name>MY-SYSTEM</name>", cpf_envelope)
        self.assertIn('root="2.16.840.1.113883.13.236"', cns_envelope)
        self.assertIn('extension="898001160366001"', cns_envelope)

