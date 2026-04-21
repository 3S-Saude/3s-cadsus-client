# cadsus_client

Biblioteca Python para simplificar a interoperabilidade de projetos Django com a API do CADSUS do Ministerio da Saude.

O pacote publicado no PyPI se chama `3s-cadsus-client`, enquanto o import no codigo Python e `cadsus_client`.

## Destaques

- Cliente 100% assincrono com `httpx.AsyncClient`
- Cache de token com suporte automatico ao cache do Django quando disponivel
- Fallback para cache em memoria quando o Django nao estiver carregado
- Autenticacao por `API` ou `CERT`
- Metodo `buscar_pessoa` com deteccao automatica de CPF ou CNS
- Conversao do retorno SOAP em dicionario Python com `resultado.json()`
- Workflow de GitHub Actions pronto para build, teste e publicacao no PyPI

## Instalacao

```bash
pip install 3s-cadsus-client
```

## Configuracao por variaveis de ambiente

### Metodo API

```bash
export CADSUS_AUTH_METHOD=API
export CADSUS_AUTH_LOGIN_URL=https://ses-token-api-linux.azurewebsites.net/login
export CADSUS_AUTH_TOKEN_URL=https://ses-token-api-linux.azurewebsites.net/token/osb
export CADSUS_API_URL=https://servicos.saude.gov.br/cadsus/v2/PDQSupplierJWT
export CADSUS_USER=user_cadsus
export CADSUS_PASSWORD=password_cadsus
```

### Metodo CERT

```bash
export CADSUS_AUTH_METHOD=CERT
export CADSUS_AUTH_TOKEN_URL=https://ehr-auth.saude.gov.br/api/osb/token
export CADSUS_API_URL=https://servicos.saude.gov.br/cadsus/v2/PDQSupplierJWT
export CADSUS_CERT=/srv/app/cert.pem
export CADSUS_KEY=/srv/app/key.pem
```

### Variaveis opcionais

```bash
export CADSUS_SYSTEM_CODE=CADSUS
export CADSUS_TIMEOUT=30
export CADSUS_CACHE_ALIAS=default
export CADSUS_CACHE_PREFIX=cadsus_client
export CADSUS_TOKEN_TTL_FALLBACK=300
```

## Uso rapido

```python
from cadsus_client import CadSUSClient


async def consultar_cadsus(identificador: str) -> dict | None:
    async with CadSUSClient.from_env() as client:
        resultado = await client.buscar_pessoa(identificador)
        return resultado.json()
```

## Exemplo em projeto Django

```python
from django.http import JsonResponse

from cadsus_client import CadSUSClient


async def buscar_pessoa_view(request):
    identificador = request.GET["identificador"]

    async with CadSUSClient.from_env() as client:
        resultado = await client.buscar_pessoa(identificador)
        payload = resultado.json()

    return JsonResponse(
        {
            "document_type": resultado.document_type.value,
            "identifier": resultado.normalized_identifier,
            "dados": payload,
        }
    )
```

Tambem existe um atalho para obter diretamente o dicionario parseado:

```python
from cadsus_client import CadSUSClient


async def consultar_cadsus(identificador: str) -> dict | None:
    async with CadSUSClient.from_env() as client:
        return await client.buscar_pessoa_json(identificador)
```

## Cache de token

Quando o Django estiver disponivel e configurado, a biblioteca usa `django.core.cache` automaticamente. Caso contrario, usa um cache em memoria do processo.

O tempo de expiracao do token e resolvido na seguinte ordem:

1. Campo `expires_in` da resposta de autenticacao
2. Claim `exp` do JWT retornado
3. Fallback configurado por `CADSUS_TOKEN_TTL_FALLBACK`

## Personalizacao avancada da autenticacao API

Como a especificacao recebida nao detalha o payload exato dos dois POSTs do metodo `API`, a biblioteca aplica o fluxo padrao abaixo:

1. `POST CADSUS_AUTH_LOGIN_URL` com JSON `{"username": "...", "password": "..."}`
2. `POST CADSUS_AUTH_TOKEN_URL` com header `Authorization: Bearer <token-do-login>`

Se a sua instalacao exigir outro formato, voce pode customizar os requests:

```python
from cadsus_client import CadSUSClient, RequestDefinition


def build_login_request(settings):
    return RequestDefinition(
        method="POST",
        url=settings.auth_login_url,
        data={"user": settings.user, "password": settings.password},
    )


def build_token_request(settings, login_token):
    return RequestDefinition(
        method="POST",
        url=settings.auth_token_url,
        headers={"Authorization": f"Bearer {login_token}"},
        json={"grant_type": "client_credentials"},
    )


async def consultar(identificador: str):
    async with CadSUSClient.from_env(
        api_login_request_factory=build_login_request,
        api_token_request_factory=build_token_request,
    ) as client:
        return await client.buscar_pessoa(identificador)
```

## Publicacao no PyPI

O workflow em `.github/workflows/publish.yml` roda testes em `push` e `pull_request`, gera os artefatos do pacote e publica no PyPI quando uma tag `v*` e enviada.

O fluxo foi montado para usar Trusted Publishing via OIDC. Antes da primeira release, cadastre o repositrio e o workflow no PyPI:

- https://packaging.python.org/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/
- https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-pypi
