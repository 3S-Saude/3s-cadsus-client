from .auth import RequestDefinition
from .client import CadSUSClient, DocumentType, buscar_pessoa, buscar_pessoa_debug
from .config import AuthMethod, CadSUSSettings
from .exceptions import (
    CadSUSAuthenticationError,
    CadSUSConfigurationError,
    CadSUSParseError,
    CadSUSError,
    CadSUSRequestError,
)

__all__ = [
    "AuthMethod",
    "CadSUSAuthenticationError",
    "CadSUSClient",
    "CadSUSConfigurationError",
    "CadSUSParseError",
    "CadSUSError",
    "CadSUSRequestError",
    "CadSUSSettings",
    "DocumentType",
    "RequestDefinition",
    "buscar_pessoa",
    "buscar_pessoa_debug",
]
