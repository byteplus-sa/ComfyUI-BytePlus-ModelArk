import asyncio
from urllib.parse import urlsplit

import aiohttp

from .constants import (
    LAS_REGION_BASE_URLS,
    MEDIAKIT_REGION_BASE_URLS,
    OMNIHUMAN_BASE_URL,
    REGION_BASE_URLS,
    SPEECH_REGION_BASE_URLS,
)
from .nodes_shared import BytePlusException, sleep_interruptible, wait_interruptible
from .utils_download import public_download_session, validate_public_url


class RetryableRequestError(BytePlusException):
    """
    A failed service request. retryable: network errors, timeouts, 429 and 5xx.
    status: the HTTP status, if any.
    """

    def __init__(self, message, retryable=False, status=None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


def is_retryable_status(status):
    return status == 429 or status >= 500


def _origin(url):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port or 443


def _validate_credential_destination(url, headers):
    providers = {
        "authorization": (
            *REGION_BASE_URLS.values(),
            *MEDIAKIT_REGION_BASE_URLS.values(),
            *LAS_REGION_BASE_URLS.values(),
            OMNIHUMAN_BASE_URL,
        ),
        "x-api-key": tuple(SPEECH_REGION_BASE_URLS.values()),
    }
    for name, value in headers.items():
        allowed = providers.get(name.lower())
        if allowed is None:
            continue
        if _origin(url) not in {_origin(base) for base in allowed}:
            raise aiohttp.InvalidURL("API credentials may only be sent to their BytePlus HTTPS service")
        if not isinstance(value, str) or any(ord(character) < 32 or ord(character) >= 127 for character in value):
            raise aiohttp.InvalidURL("Invalid API credential header")


async def send_http_request(method, url, headers, body, timeout_seconds):
    """One HTTP request with an optional JSON body: (status, response headers, response bytes)."""
    validate_public_url(url)
    _validate_credential_destination(url, headers)
    client_timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with public_download_session(timeout=client_timeout) as session:
        async with session.request(method, url, headers=headers, json=body, allow_redirects=False) as response:
            return response.status, dict(response.headers), await response.read()


async def await_request(send, timeout_message, network_message):
    """
    Await a send coroutine, honouring ComfyUI interrupts. Timeouts and network
    errors become retryable RetryableRequestErrors; network_message(error) is
    the text for the latter.
    """
    try:
        return await wait_interruptible(send)
    except asyncio.TimeoutError:
        raise RetryableRequestError(timeout_message, retryable=True) from None
    except aiohttp.ClientError as e:
        raise RetryableRequestError(network_message(e), retryable=not isinstance(e, aiohttp.InvalidURL)) from e


async def retry_transient(request, *, max_errors, poll_seconds):
    """
    Await request() for a status query of a submitted (possibly already billed)
    task: retryable failures are retried after poll_seconds, up to max_errors in
    a row, instead of abandoning the task. Other errors are raised at once.
    """
    errors = 0
    while True:
        try:
            return await request()
        except RetryableRequestError as e:
            errors += 1
            if not e.retryable or errors >= max_errors:
                raise
            await sleep_interruptible(poll_seconds)
