import aiohttp
import pytest
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from pool_prices.retry import backoff_delay, is_transient, retry


def http_error(status: int) -> aiohttp.ClientResponseError:
    info = aiohttp.RequestInfo(
        URL("http://x"), "POST", CIMultiDictProxy(CIMultiDict()), URL("http://x")
    )
    return aiohttp.ClientResponseError(info, (), status=status)


class Flaky:
    def __init__(self, failures: int, error: Exception) -> None:
        self.failures, self.error, self.calls = failures, error, 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return "ok"


async def test_succeeds_after_transient_failures_with_growing_delays() -> None:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    flaky = Flaky(2, TimeoutError())
    result = await retry(
        flaky, attempts=3, base_delay=1.0, max_delay=10.0, sleep=fake_sleep, rng=lambda: 1.0
    )
    assert (result, flaky.calls) == ("ok", 3)
    assert delays == [1.0, 2.0]


async def test_raises_last_error_after_exhausting_attempts() -> None:
    flaky = Flaky(10, TimeoutError("slow"))
    with pytest.raises(TimeoutError, match="slow"):
        await retry(flaky, attempts=3, sleep=_no_sleep)
    assert flaky.calls == 3


async def test_non_retryable_error_fails_immediately() -> None:
    flaky = Flaky(10, ValueError("bad input"))
    with pytest.raises(ValueError):
        await retry(flaky, attempts=5, sleep=_no_sleep)
    assert flaky.calls == 1


async def test_attempts_below_one_is_rejected() -> None:
    with pytest.raises(ValueError):
        await retry(Flaky(0, TimeoutError()), attempts=0)


async def _no_sleep(_: float) -> None:
    return None


def test_backoff_is_capped_and_jittered() -> None:
    assert backoff_delay(10, 1.0, 5.0, rng=1.0) == 5.0
    assert backoff_delay(3, 1.0, 5.0, rng=0.0) == 0.0
    assert backoff_delay(1, 0.5, 5.0, rng=0.5) == 0.5


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_statuses(status: int) -> None:
    assert is_transient(http_error(status))


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(status: int) -> None:
    assert not is_transient(http_error(status))


def test_is_transient_for_network_errors() -> None:
    assert is_transient(TimeoutError())
    assert is_transient(aiohttp.ServerDisconnectedError())
    assert not is_transient(RuntimeError("boom"))
