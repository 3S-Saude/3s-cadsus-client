class CadSUSError(Exception):
    """Base exception for the package."""


class CadSUSConfigurationError(CadSUSError):
    """Raised when required settings are missing or invalid."""


class CadSUSAuthenticationError(CadSUSError):
    """Raised when the authentication flow fails."""


class CadSUSRequestError(CadSUSError):
    """Raised when a request to the CADSUS API fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body

