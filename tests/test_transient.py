"""`transient_failure`: which failures of a model's API are worth asking again, and how long the
API asked to wait. Read off the SDK's own exception beneath a provider's `ProviderError`, by
status and class name, never by message (tests/sdk_errors.py has the stand-ins)."""

from __future__ import annotations

import email.utils
import time

import pytest

from quackd.agent.providers.base import (
    NO_ANSWER_IN_TIME,
    NO_CONNECTION,
    ProviderError,
    ProviderNotInstalled,
    transient_failure,
)

from . import sdk_errors as sdk


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 529])
def test_a_status_worth_asking_again_is_transient(status: int) -> None:
    failure = transient_failure(sdk.wrapped(sdk.APIStatusError(status)))
    assert failure is not None and failure.status == status, failure
    assert f"({status})" in failure.why


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 413, 422, 308, 200])
def test_a_status_that_says_no_is_permanent(status: int) -> None:
    """A bad request, a key that does not work, a model that does not exist, a request too big,
    a refused redirect, and 409, which both SDKs retry and quackd does not: none of them is
    answered differently by asking the same request again."""
    assert transient_failure(sdk.wrapped(sdk.APIStatusError(status))) is None


def test_the_overloaded_status_reads_the_same_through_either_sdk() -> None:
    """Anthropic raises a 529 as `OverloadedError`, openai as `InternalServerError`: the status
    is what is read, so both are the same failure."""
    for error in (sdk.OverloadedError(529), sdk.InternalServerError(529)):
        failure = transient_failure(sdk.wrapped(error))
        assert failure is not None and failure.why == "overloaded (529)", failure


def test_x_should_retry_false_makes_a_server_error_permanent() -> None:
    error = sdk.InternalServerError(503, {"x-should-retry": "false"})
    assert transient_failure(sdk.wrapped(error)) is None
    error = sdk.InternalServerError(503, {"x-should-retry": "true"})
    assert transient_failure(sdk.wrapped(error)) is not None


def test_retry_after_ms_wins_over_seconds_and_a_date_is_read() -> None:
    both = sdk.RateLimitError(429, {"retry-after-ms": "1500", "retry-after": "20"})
    assert transient_failure(sdk.wrapped(both)).retry_after_s == 1.5  # type: ignore[union-attr]
    seconds = sdk.RateLimitError(429, {"retry-after": "20"})
    assert transient_failure(sdk.wrapped(seconds)).retry_after_s == 20.0  # type: ignore[union-attr]
    later = email.utils.formatdate(time.time() + 30, usegmt=True)
    dated = transient_failure(sdk.wrapped(sdk.RateLimitError(429, {"retry-after": later})))
    assert dated is not None and dated.retry_after_s is not None
    assert 25 < dated.retry_after_s <= 31, dated


@pytest.mark.parametrize("value", ["0", "-3", "nan", "inf", "soon"])
def test_a_retry_after_that_is_not_a_wait_is_no_ask(value: str) -> None:
    failure = transient_failure(sdk.wrapped(sdk.RateLimitError(429, {"retry-after": value})))
    assert failure is not None and failure.retry_after_s is None, failure


def test_a_date_already_past_is_no_ask() -> None:
    gone = email.utils.formatdate(time.time() - 30, usegmt=True)
    failure = transient_failure(sdk.wrapped(sdk.RateLimitError(429, {"retry-after": gone})))
    assert failure is not None and failure.retry_after_s is None


def test_gemini_retry_info_is_the_wait_it_asked_for() -> None:
    details = {
        "error": {
            "details": [
                {"@type": "type.googleapis.com/google.rpc.Help"},
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "39s"},
            ]
        }
    }
    error = sdk.ClientError(429, "RESOURCE_EXHAUSTED", details)
    failure = transient_failure(sdk.wrapped(error))
    assert failure is not None and failure.status == 429 and failure.retry_after_s == 39.0


def test_genai_status_word_is_never_read_as_a_status() -> None:
    """google-genai's `status` is a word (`RESOURCE_EXHAUSTED`); `code` is the number."""
    failure = transient_failure(sdk.wrapped(sdk.ServerError(503, "UNAVAILABLE")))
    assert failure is not None and failure.status == 503


def test_a_bool_status_is_no_status() -> None:
    class Odd(Exception):
        status_code = True

    assert transient_failure(sdk.wrapped(Odd())) is None


@pytest.mark.parametrize(
    ("error", "why"),
    [
        (sdk.APITimeoutError(), NO_ANSWER_IN_TIME),
        (sdk.ReadTimeout(), NO_ANSWER_IN_TIME),
        (TimeoutError(), NO_ANSWER_IN_TIME),
        (sdk.APIConnectionError(), NO_CONNECTION),
        (sdk.ConnectError(), NO_CONNECTION),
        (sdk.RemoteProtocolError(), NO_CONNECTION),
        (ConnectionResetError(), NO_CONNECTION),
    ],
)
def test_no_answer_errors_are_transient_by_their_class(error: BaseException, why: str) -> None:
    failure = transient_failure(sdk.wrapped(error))
    assert failure is not None and failure.why == why and failure.status is None, failure


@pytest.mark.parametrize("built_wrong", [sdk.LocalProtocolError, sdk.UnsupportedProtocol])
def test_a_request_quackd_built_wrong_is_not_asked_again(built_wrong: type[Exception]) -> None:
    """Bare, and as the SDKs really raise one: their transport wraps every failure inside it as
    an `APIConnectionError` raised from it, so read by its outer name alone a request quackd
    built wrong would be a lost connection, and asked again."""
    assert transient_failure(sdk.wrapped(built_wrong())) is None
    under = sdk.caused(sdk.APIConnectionError("Connection error."), built_wrong())
    assert transient_failure(sdk.wrapped(under)) is None


def test_a_connection_the_sdk_lost_is_transient_through_its_real_chain() -> None:
    """A refused connection as the SDKs raise it: their `APIConnectionError` from httpx's
    `ConnectError`."""
    under = sdk.caused(sdk.APIConnectionError("Connection error."), sdk.ConnectError())
    failure = transient_failure(sdk.wrapped(under))
    assert failure is not None and failure.why == NO_CONNECTION, failure


def test_the_cause_is_walked_and_the_first_status_decides() -> None:
    """A status found anywhere down the chain decides, and the nearest one first."""
    deep = sdk.wrapped(sdk.wrapped(sdk.RateLimitError(429), "a provider's"), "a gateway's")
    assert transient_failure(deep) is not None
    nearest = sdk.BadRequestError(400)
    nearest.__cause__ = sdk.RateLimitError(429)
    assert transient_failure(sdk.wrapped(nearest)) is None, "a farther status overrode a nearer"


def test_an_implicit_context_is_not_walked() -> None:
    """Raised while handling a rate limit is not raised because of one."""
    error = ProviderError("a bug while handling the answer")
    error.__context__ = sdk.RateLimitError(429)
    assert transient_failure(error) is None


@pytest.mark.parametrize(
    "text",
    [
        "anthropic: rate limited (retry-after 30s)",
        "openai: InternalServerError: overloaded",
        "the server timed out",
    ],
)
def test_a_bare_provider_error_is_never_asked_again_whatever_it_says(text: str) -> None:
    """Every error test in the suite raises a bare `ProviderError` and expects the run to end at
    once, and so does every refusal quackd writes itself. The message is never read."""
    assert transient_failure(ProviderError(text)) is None


def test_quackds_own_refusals_are_permanent() -> None:
    assert transient_failure(ProviderNotInstalled("anthropic", "anthropic")) is None
    redirect = sdk.wrapped(sdk.APIStatusError(308, {"location": "https://elsewhere"}))
    assert transient_failure(redirect) is None
    parse = sdk.wrapped(ValueError("not JSON"))
    assert transient_failure(parse) is None


def test_a_cause_cycle_ends() -> None:
    first, second = ProviderError("one"), ProviderError("two")
    first.__cause__, second.__cause__ = second, first
    assert transient_failure(first) is None


def test_x_should_retry_true_makes_any_status_worth_asking_again() -> None:
    """Both SDKs obey the header either way, and with their own retries off (`NO_SDK_RETRIES`)
    it is read here: `true` asks a status that is worth nothing by itself again, in words that
    say the API asked for it."""
    failure = transient_failure(sdk.wrapped(sdk.BadRequestError(400, {"x-should-retry": "true"})))
    assert failure is not None and failure.status == 400, failure
    assert failure.why == "the API asked to be asked again (400)", failure.why
