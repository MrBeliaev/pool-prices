from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from aiohttp import ClientTimeout
from web3 import AsyncHTTPProvider, AsyncWeb3


def make_web3(rpc_url: str, timeout_seconds: float = 10.0) -> AsyncWeb3[AsyncHTTPProvider]:
    provider = AsyncHTTPProvider(
        rpc_url,
        request_kwargs={"timeout": ClientTimeout(total=timeout_seconds)},
        exception_retry_configuration=None,
    )

    return AsyncWeb3(provider)


@asynccontextmanager
async def open_web3(
    rpc_url: str, timeout_seconds: float = 10.0
) -> AsyncGenerator[AsyncWeb3[AsyncHTTPProvider]]:
    w3 = make_web3(rpc_url, timeout_seconds)
    try:
        yield w3
    finally:
        await w3.provider.disconnect()
