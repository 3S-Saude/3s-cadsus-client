from __future__ import annotations

import base64
import json
import time
import unittest

import httpx

from cadsus_client import CadSUSClient, CadSUSParseError, CadSUSSettings
from cadsus_client.client import DocumentType, get_document_type, normalize_identifier
from cadsus_client.config import AuthMethod
from cadsus_client.soap import SoapDocumentType, build_busca_pessoa_envelope, parse_busca_pessoa_response


SOAP_RESPONSE = """\
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:S="http://www.w3.org/2003/05/soap-envelope" xmlns="urn:hl7-org:v3">
  <S:Body>
    <PRPA_IN201306UV02>
      <controlActProcess>
        <subject>
          <registrationEvent>
            <subject1>
              <patient>
                <patientPerson>
                  <name>
                    <given>MARIA</given>
                  </name>
                  <raceCode code="01" />
                  <birthTime value="19870115" />
                  <administrativeGenderCode code="F" />
                  <addr>
                    <streetName>Rua das Flores</streetName>
                    <additionalLocator>Centro</additionalLocator>
                    <city>2611606</city>
                    <postalCode>50000000</postalCode>
                    <houseNumber>123</houseNumber>
                  </addr>
                  <telecom value="mailto:maria@example.com" />
                  <telecom value="tel:+55-81-98765-4321" />
                  <asOtherIDs>
                    <id root="2.16.840.1.113883.13.236" extension="898001160366001" />
                  </asOtherIDs>
                  <asOtherIDs>
                    <id root="2.16.840.1.113883.13.236" extension="898001160366002" />
                  </asOtherIDs>
                  <asOtherIDs>
                    <id root="2.16.840.1.113883.13.237" extension="12345678901" />
                  </asOtherIDs>
                  <personalRelationship>
                    <code code="MTH" />
                    <relationshipHolder1>
                      <name>
                        <given>JOSEFA</given>
                      </name>
                    </relationshipHolder1>
                  </personalRelationship>
                  <deceasedInd value="false" />
                </patientPerson>
              </patient>
            </subject1>
          </registrationEvent>
        </subject>
      </controlActProcess>
    </PRPA_IN201306UV02>
  </S:Body>
</soap:Envelope>
"""


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

        self.assertIsNone(result)
        self.assertEqual(counters["token"], 1)

    async def test_buscar_pessoa_returns_structured_data(self) -> None:
        jwt_token = build_fake_jwt(int(time.time()) + 3600)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url == httpx.URL("https://example.test/token"):
                return httpx.Response(200, json={"access_token": jwt_token})
            if request.url == httpx.URL("https://example.test/api"):
                return httpx.Response(200, text=SOAP_RESPONSE)
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
            parsed = await client.buscar_pessoa("12345678901")

        expected = {
            "nome": "MARIA",
            "raca_cor": "01",
            "data_nascimento": "1987-01-15",
            "sexo": "F",
            "logradouro": "Rua das Flores",
            "bairro": "Centro",
            "ibge": "2611606",
            "cep": "50000000",
            "numero": "123",
            "telefone": "(81)98765-4321",
            "lista_cns": ["898001160366001", "898001160366002"],
            "cns": "898001160366001",
            "cpf": "123.456.789-01",
            "nome_da_mae": "JOSEFA",
            "falecido": False,
            "data_falecimento": None,
        }
        self.assertEqual(parsed, expected)


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

    def test_parse_busca_pessoa_response_returns_none_without_body(self) -> None:
        xml = '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Header /></soap:Envelope>'

        self.assertIsNone(parse_busca_pessoa_response(xml))

    def test_parse_busca_pessoa_response_raises_for_invalid_xml(self) -> None:
        with self.assertRaises(CadSUSParseError):
            parse_busca_pessoa_response("<invalid")
