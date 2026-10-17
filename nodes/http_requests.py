import asyncio

import aiohttp

from .nodes_shared import BytePlusException, sleep_interruptible, wait_interruptible


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


async def send_http_request(method, url, headers, body, timeout_seconds):
    """One HTTP request with an optional JSON body: (status, response headers, response bytes)."""
    client_timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.request(method, url, headers=headers, json=body) as response:
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
        raise RetryableRequestError(network_message(e), retryable=True) from e


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
