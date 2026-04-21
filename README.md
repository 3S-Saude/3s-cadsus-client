# cadsus_client

Biblioteca Python para simplificar a interoperabilidade de projetos Django com a API do CADSUS do Ministerio da Saude.

O pacote publicado no PyPI se chama `3s-cadsus-client`, enquanto o import no codigo Python e `cadsus_client`.

## Destaques

- Cliente 100% assincrono com `httpx.AsyncClient`
- Cache de token usando exclusivamente `django.core.cache`
- Autenticacao por `API` ou `CERT`
- Metodo `buscar_pessoa` com deteccao automatica de CPF ou CNS e retorno direto em JSON
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

No fluxo `CERT`, a biblioteca consulta `CADSUS_AUTH_TOKEN_URL` com `GET`, apresentando o certificado configurado no client TLS.

### Variaveis opcionais

```bash
export CADSUS_SYSTEM_CODE=CADSUS
export CADSUS_TIMEOUT=30
export CADSUS_CACHE_ALIAS=default
export CADSUS_TOKEN_TTL_FALLBACK=300
```

## Uso rapido

```python
from cadsus_client import CadSUSClient


async def consultar_cadsus(identificador: str) -> dict | None:
    async with CadSUSClient.from_env() as client:
        return await client.buscar_pessoa(identificador)
```

## Modo de debug

Para investigar falhas de autenticacao ou da consulta ao CADSUS, o pacote expoe `buscar_pessoa_debug`.

O metodo imprime no terminal:

- Variaveis usadas no fluxo
- Status do cache de token
- Requests e responses do login, do `CADSUS_AUTH_TOKEN_URL` e da API do CADSUS
- Excecoes levantadas durante autenticacao, consulta e parse

```python
from cadsus_client import buscar_pessoa_debug


payload = await buscar_pessoa_debug(
    "12345678901",
    reveal_secrets=True,
)
```

Se preferir usar uma instancia ja criada do cliente:

```python
async with CadSUSClient.from_env() as client:
    payload = await client.buscar_pessoa_debug(
        "12345678901",
        reveal_secrets=True,
    )
```

Use `reveal_secrets=True` apenas em diagnostico controlado, pois essa opcao imprime senha, tokens e headers sensiveis no `stdout`.

## Exemplo em projeto Django

```python
from django.http import JsonResponse

from cadsus_client import CadSUSClient


async def buscar_pessoa_view(request):
    identificador = request.GET["identificador"]

    async with CadSUSClient.from_env() as client:
        payload = await client.buscar_pessoa(identificador)

    return JsonResponse({"dados": payload})
```

## Cache de token

A biblioteca usa exclusivamente `django.core.cache` para armazenar o token.

A chave utilizada no backend configurado para esse cache, incluindo Redis, e `cadsus_token`.

O alias do cache do Django continua configuravel por `CADSUS_CACHE_ALIAS`.

Nos fluxos `API` e `CERT`, o tempo de expiracao do token em cache e definido pela claim `exp` do JWT retornado pelo endpoint `CADSUS_AUTH_TOKEN_URL`.

Se o token retornado nao trouxer uma claim `exp` legivel, a biblioteca usa o fallback configurado por `CADSUS_TOKEN_TTL_FALLBACK`.

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
