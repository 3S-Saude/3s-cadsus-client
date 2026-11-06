import asyncio
from contextlib import asynccontextmanager
import unittest

import httpx
from cadsus_client import CadSUSClient, CadSUSSettings
from cadsus_client.config import AuthMethod
from test_client import FakeTokenCache, build_fake_jwt


class ResilienciaTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_chega_na_autenticacao_e_consulta(self):
        fases = []
        def responder(req):
            fases.append(req.extensions['timeout'])
            if str(req.url).endswith('/api'):
                return httpx.Response(200, text='<ok/>')
            return httpx.Response(200, json={'access_token': build_fake_jwt()})
        settings = CadSUSSettings(auth_method=AuthMethod.API, auth_login_url='https://teste.invalid/login',
            auth_token_url='https://teste.invalid/token', api_url='https://teste.invalid/api', user='teste', password='teste')
        timeout = httpx.Timeout(1, connect=0.5, pool=0.05)
        @asynccontextmanager
        async def lock():
            yield
        async with CadSUSClient(settings, cache=FakeTokenCache(), http_timeout=timeout, token_lock=lock,
                                transport=httpx.MockTransport(responder)) as client:
            await client.buscar_pessoa('12345678901')
        self.assertEqual(len(fases), 3)
        self.assertTrue(all(f == timeout.as_dict() for f in fases))


class VersaoDistribuicaoTest(unittest.TestCase):
    def test_versao_publica_corresponde_a_distribuicao_instalada(self):
        from importlib.metadata import version
        import cadsus_client
        self.assertEqual(cadsus_client.__version__, version("3s-cadsus-client"))


class AusenciaConfirmadaTests(unittest.TestCase):
    def test_fault_e_estrutura_inesperada_nao_sao_ausencia(self):
        from cadsus_client.soap import parse_busca_pessoa_response
        from cadsus_client.exceptions import CadSUSParseError
        for xml in ('<ok/>', '<Envelope><Body><Fault/></Body></Envelope>'):
            with self.subTest(xml=xml), self.assertRaises(CadSUSParseError):
                parse_busca_pessoa_response(xml, validar_resposta=True)

    def test_resposta_pdq_vazia_e_ausencia_confirmada(self):
        from cadsus_client.soap import parse_busca_pessoa_response
        xml = '<Envelope><Body><PRPA_IN201306UV02/></Body></Envelope>'
        self.assertIsNone(parse_busca_pessoa_response(xml, validar_resposta=True))


class IdentidadeRespostaTests(unittest.IsolatedAsyncioTestCase):
    async def test_valida_nome_e_documento_do_paciente(self):
        import time
        from cadsus_client.cache import CachedToken
        from cadsus_client.exceptions import CadSUSParseError
        from test_client import SOAP_RESPONSE
        settings = CadSUSSettings(auth_method=AuthMethod.API, auth_login_url='https://teste.invalid/login',
            auth_token_url='https://teste.invalid/token', api_url='https://teste.invalid/api', user='teste', password='teste')
        for xml, valido in ((SOAP_RESPONSE, True), (SOAP_RESPONSE.replace('12345678901', '99999999999'), False),
                            (SOAP_RESPONSE.replace('<given>MARIA</given>', '<given/>'), False)):
            cache = FakeTokenCache()
            await cache.set(settings.cache_key, CachedToken(value='token', expires_at=time.time()+300))
            async with CadSUSClient(settings, cache=cache, transport=httpx.MockTransport(
                lambda req: httpx.Response(200, text=xml))) as client:
                if valido:
                    self.assertEqual((await client.buscar_pessoa('12345678901', validar_resposta=True))['nome'], 'MARIA')
                else:
                    with self.assertRaises(CadSUSParseError):
                        await client.buscar_pessoa('12345678901', validar_resposta=True)
