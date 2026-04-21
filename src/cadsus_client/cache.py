from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

from .config import CadSUSSettings
from .exceptions import CadSUSConfigurationError


@dataclass(frozen=True, slots=True)
class CachedToken:
    value: str
    expires_at: float


class TokenCache(Protocol):
    async def get(self, key: str) -> CachedToken | None:
        ...

    async def set(self, key: str, token: CachedToken) -> None:
        ...


class DjangoTokenCache:
    def __init__(self, alias: str) -> None:
        try:
            from django.core.cache import caches
        except Exception as exc:
            raise CadSUSConfigurationError(
                "O cache de token exige Django com o cache configurado."
            ) from exc

        try:
            self._cache = caches[alias]
        except Exception as exc:
            raise CadSUSConfigurationError(
                f"Nao foi possivel carregar o cache do Django com alias '{alias}'."
            ) from exc

    async def get(self, key: str) -> CachedToken | None:
        payload = await asyncio.to_thread(self._cache.get, key)
        if not payload:
            return None
        entry = CachedToken(
            value=payload["value"],
            expires_at=float(payload["expires_at"]),
        )
        if entry.expires_at <= time.time():
            await asyncio.to_thread(self._cache.delete, key)
            return None
        return entry

    async def set(self, key: str, token: CachedToken) -> None:
        ttl = max(1, int(token.expires_at - time.time()))
        payload = {"value": token.value, "expires_at": token.expires_at}
        await asyncio.to_thread(self._cache.set, key, payload, ttl)


def create_token_cache(settings: CadSUSSettings) -> TokenCache:
    return DjangoTokenCache(settings.cache_alias)
