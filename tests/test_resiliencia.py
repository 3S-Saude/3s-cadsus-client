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
