from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .exceptions import CadSUSConfigurationError


class AuthMethod(str, Enum):
    API = "API"
    CERT = "CERT"

    @classmethod
    def from_value(cls, value: str | None) -> "AuthMethod":
        normalized = (value or cls.API.value).strip().upper()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise CadSUSConfigurationError(
                "CADSUS_AUTH_METHOD deve ser API ou CERT."
            ) from exc


@dataclass(frozen=True, slots=True)
class CadSUSSettings:
    auth_method: AuthMethod
    auth_token_url: str
    api_url: str
    auth_login_url: str | None = None
    user: str | None = None
    password: str | None = None
    cert: str | None = None
    key: str | None = None
    system_code: str = "CADSUS"
    timeout: float = 30.0
    cache_alias: str = "default"
    cache_prefix: str = "3s_cadsus_client"
    token_ttl_fallback: int = 300

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "CadSUSSettings":
        env = environ if environ is not None else os.environ
        settings = cls(
            auth_method=AuthMethod.from_value(env.get("CADSUS_AUTH_METHOD")),
            auth_login_url=env.get("CADSUS_AUTH_LOGIN_URL"),
            auth_token_url=_required(env, "CADSUS_AUTH_TOKEN_URL"),
            api_url=_required(env, "CADSUS_API_URL"),
            user=env.get("CADSUS_USER"),
            password=env.get("CADSUS_PASSWORD"),
            cert=env.get("CADSUS_CERT"),
            key=env.get("CADSUS_KEY"),
            system_code=env.get("CADSUS_SYSTEM_CODE", "CADSUS"),
            timeout=float(env.get("CADSUS_TIMEOUT", "30")),
            cache_alias=env.get("CADSUS_CACHE_ALIAS", "default"),
            cache_prefix=env.get("CADSUS_CACHE_PREFIX", "3s_cadsus_client"),
            token_ttl_fallback=int(env.get("CADSUS_TOKEN_TTL_FALLBACK", "300")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.timeout <= 0:
            raise CadSUSConfigurationError("CADSUS_TIMEOUT deve ser maior que zero.")
        if self.token_ttl_fallback <= 0:
            raise CadSUSConfigurationError(
                "CADSUS_TOKEN_TTL_FALLBACK deve ser maior que zero."
            )
        if self.auth_method is AuthMethod.API:
            missing = []
            if not self.auth_login_url:
                missing.append("CADSUS_AUTH_LOGIN_URL")
            if not self.user:
                missing.append("CADSUS_USER")
            if not self.password:
                missing.append("CADSUS_PASSWORD")
            if missing:
                raise CadSUSConfigurationError(
                    "Metodo API requer as variaveis: " + ", ".join(missing)
                )
            return

        missing = []
        if not self.cert:
            missing.append("CADSUS_CERT")
        if not self.key:
            missing.append("CADSUS_KEY")
        if missing:
            raise CadSUSConfigurationError(
                "Metodo CERT requer as variaveis: " + ", ".join(missing)
            )

    @property
    def cache_key(self) -> str:
        material = "|".join(
            [
                self.auth_method.value,
                self.auth_login_url or "",
                self.auth_token_url,
                self.api_url,
                self.user or "",
                self.password or "",
                self.cert or "",
                self.key or "",
            ]
        )
        fingerprint = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return f"{self.cache_prefix}:token:{fingerprint}"


def _required(environ: Mapping[str, str], key: str) -> str:
    value = environ.get(key)
    if not value:
        raise CadSUSConfigurationError(f"Variavel obrigatoria ausente: {key}")
    return value
