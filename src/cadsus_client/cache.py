from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

from .config import CadSUSSettings


@dataclass(frozen=True, slots=True)
class CachedToken:
    value: str
    expires_at: float


class TokenCache(Protocol):
    async def get(self, key: str) -> CachedToken | None:
        ...

    async def set(self, key: str, token: CachedToken) -> None:
        ...


_MEMORY_STORE: dict[str, CachedToken] = {}
_MEMORY_LOCK = asyncio.Lock()


class InMemoryTokenCache:
    async def get(self, key: str) -> CachedToken | None:
        async with _MEMORY_LOCK:
            entry = _MEMORY_STORE.get(key)
            if entry is None:
                return None
            if entry.expires_at <= time.time():
                _MEMORY_STORE.pop(key, None)
                return None
            return entry

    async def set(self, key: str, token: CachedToken) -> None:
        async with _MEMORY_LOCK:
            _MEMORY_STORE[key] = token


class DjangoTokenCache:
    def __init__(self, alias: str) -> None:
        from django.core.cache import caches

        self._cache = caches[alias]

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
    try:
        return DjangoTokenCache(settings.cache_alias)
    except Exception:
        return InMemoryTokenCache()

