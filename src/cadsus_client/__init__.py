from .auth import RequestDefinition
from .client import BuscarPessoaResult, CadSUSClient, DocumentType, buscar_pessoa, buscar_pessoa_json
from .config import AuthMethod, CadSUSSettings
from .exceptions import (
    CadSUSAuthenticationError,
    CadSUSConfigurationError,
    CadSUSParseError,
    CadSUSError,
    CadSUSRequestError,
)
from .soap import parse_busca_pessoa_response

__all__ = [
    "AuthMethod",
    "BuscarPessoaResult",
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
    "buscar_pessoa_json",
    "parse_busca_pessoa_response",
]
