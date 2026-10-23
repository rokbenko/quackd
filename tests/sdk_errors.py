"""Stand-ins for the SDK exceptions a provider raises its `ProviderError` from.

CI installs no provider SDK, so these carry the class names and attributes quackd reads off
the real ones, and nothing else: anthropic 1.9.0's and openai 3.22.1's status errors
(`status_code`, `response.headers`), their connection and timeout errors, google-genai
2.25.0's `APIError` (`code`, `status`, `details`) and httpx's transport errors. The names
matter, because `transient_failure` matches on them; the hierarchies are the SDKs' own, as far
as anything here reads them. Headers are plain dicts with lowercase keys, which is how the SDKs
look them up."""

from __future__ import annotations

from types import SimpleNamespace as NS
from typing import Any

from quackd.agent.providers.base import ProviderError


class APIStatusError(Exception):
    """anthropic's and openai's base for every answer that came back with an error status."""

    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        super().__init__(f"Error code: {status_code}")
        self.status_code = status_code
        self.response = NS(status_code=status_code, headers=dict(headers or {}))


class BadRequestError(APIStatusError): ...


class AuthenticationError(APIStatusError): ...


class PermissionDeniedError(APIStatusError): ...


class NotFoundError(APIStatusError): ...


class ConflictError(APIStatusError): ...


class RequestTooLargeError(APIStatusError): ...


class UnprocessableEntityError(APIStatusError): ...


class RateLimitError(APIStatusError): ...


class InternalServerError(APIStatusError): ...


class ServiceUnavailableError(InternalServerError): ...


class OverloadedError(InternalServerError): ...


class APIConnectionError(Exception):
    """anthropic's and openai's for a request that got no connection, or lost it."""


class APITimeoutError(APIConnectionError): ...


class APIError(Exception):
    """google-genai's base: `code` is the HTTP status, `status` the word for it."""

    def __init__(self, code: int, status: str, details: Any = None) -> None:
        super().__init__(f"{code} {status}")
        self.code = code
        self.status = status
        self.details = details


class ClientError(APIError): ...


class ServerError(APIError): ...


class TransportError(Exception):
    """httpx's base for everything below."""


class TimeoutException(TransportError): ...


class ReadTimeout(TimeoutException): ...


class NetworkError(TransportError): ...


class ConnectError(NetworkError): ...


class ProtocolError(TransportError): ...


class RemoteProtocolError(ProtocolError): ...


class LocalProtocolError(ProtocolError): ...


class UnsupportedProtocol(TransportError): ...


def caused(outer: BaseException, inner: BaseException) -> BaseException:
    """`outer` raised from `inner`, the way the anthropic and openai SDKs raise their
    `APIConnectionError` from whatever failed inside their transport."""
    outer.__cause__ = inner
    return outer


def wrapped(cause: BaseException, text: str = "the provider's own words") -> ProviderError:
    """A `ProviderError` raised `from` `cause`, the way every SDK-backed provider raises one."""
    error = ProviderError(text)
    error.__cause__ = cause
    return error
