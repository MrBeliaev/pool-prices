import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

import aiohttp

logger = logging.getLogger(__name__)

RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})


def is_transient(error: BaseException) -> bool:
    if isinstance(error, aiohttp.ClientResponseError):
        return error.status in RETRYABLE_HTTP_STATUSES
    return isinstance(error, TimeoutError | aiohttp.ClientConnectionError)


def backoff_delay(attempt: int, base_delay: float, max_delay: float, rng: float) -> float:
    return float(rng * min(max_delay, base_delay * 2**attempt))


async def retry[T](
    func: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 5.0,
    retry_if: Callable[[BaseException], bool] = is_transient,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: Callable[[], float] = random.random,
) -> T:
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    for attempt in range(attempts):
        try:
            return await func()
        except Exception as error:
            if attempt == attempts - 1 or not retry_if(error):
                raise
            delay = backoff_delay(attempt, base_delay, max_delay, rng())
            logger.warning(
                "attampt %d/%d failed (%r), retrying in %.2fs", attempt + 1, attempts, error, delay
            )
            await sleep(delay)
    raise AssertionError("unreachable")
