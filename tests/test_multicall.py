import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from eth_abi.abi import decode, encode
from web3.exceptions import Web3Exception

from pool_prices.chain.multicall import (
    MULTICALL3_ADDRESS,
    Call,
    CallResult,
    aggregate3,
    selector,
)
from pool_prices.chain.node_client import open_web3
from pool_prices.dex import v2
from pool_prices.dex.v2 import (
    GET_RESERVES_SELECTOR,
    decode_reserves,
    fetch_reserves_many,
    get_amount_out,
)
from pool_prices.models import Pool, Token

WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"

FAIL = object()


def addr(i: int) -> str:
    return f"0x{i:040x}"


def reserves_data(reserve0: int, reserve1: int) -> bytes:
    return encode(["uint112", "uint112", "uint32"], [reserve0, reserve1, 1_700_000_000])


class FakeChain:
    def __init__(self) -> None:
        self.url = ""
        self.contracts: dict[str, Any] = {}
        self.chunk_sizes: list[int] = []
        self.seen_call_data: set[bytes] = set()
        self.delay = 0.0
        self.fail_requests = False
        self.http_failures: list[int] = []
        self.active = 0
        self.max_active = 0

    async def handle(self, request: web.Request) -> web.Response:
        if self.http_failures:
            return web.Response(status=self.http_failures.pop(0))
        body = await request.json()
        method = body["method"]
        if method == "eth_chainId":
            return self._ok(body, "0x1")
        if method != "eth_call":
            return self._error(body, f"unsupported method {method}")
        if self.fail_requests:
            return self._error(body, "rpc is down")

        call = body["params"][0]
        assert call["to"].lower() == MULTICALL3_ADDRESS.lower()
        data = bytes.fromhex(call.get("data", call.get("input"))[2:])
        assert data[:4] == selector("aggregate3((address,bool,bytes)[])")
        (calls,) = decode(["(address,bool,bytes)[]"], data[4:])

        self.chunk_sizes.append(len(calls))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            results = []
            for target, _allow_failure, call_data in calls:
                self.seen_call_data.add(call_data)
                response = self.contracts.get(target.lower(), b"")
                results.append((False, b"") if response is FAIL else (True, response))
        finally:
            self.active -= 1
        return self._ok(body, "0x" + encode(["(bool,bytes)[]"], [results]).hex())

    @staticmethod
    def _ok(body: dict[str, Any], result: Any) -> web.Response:
        return web.json_response({"jsonrpc": "2.0", "id": body["id"], "result": result})

    @staticmethod
    def _error(body: dict[str, Any], message: str) -> web.Response:
        return web.json_response(
            {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32000, "message": message}}
        )


@pytest.fixture
async def chain() -> AsyncIterator[FakeChain]:
    fake = FakeChain()
    app = web.Application()
    app.router.add_post("/", fake.handle)
    server = TestServer(app)
    await server.start_server()
    fake.url = str(server.make_url("/"))
    try:
        yield fake
    finally:
        await server.close()


def test_selectors_match_known_values() -> None:
    assert selector("getReserves()").hex() == "0902f1ac"
    assert selector("aggregate3((address,bool,bytes)[])").hex() == "82ad56cb"


def test_decode_reserves_returns_none_for_empty_or_short_data() -> None:
    assert decode_reserves(b"") is None
    assert decode_reserves(b"\x00" * 95) is None
    assert decode_reserves(reserves_data(5, 7)) == (5, 7)


async def test_aggregate3_keeps_order_across_chunks_and_limits_concurrency(
    chain: FakeChain,
) -> None:
    chain.delay = 0.05
    for i in range(10):
        chain.contracts[addr(i)] = i.to_bytes(32, "big")
    calls = [Call(target=addr(i), call_data=GET_RESERVES_SELECTOR) for i in range(10)]

    async with open_web3(chain.url) as w3:
        results = await aggregate3(w3, calls, chunk_size=3, max_concurrent=2)

    assert [int.from_bytes(r.return_data, "big") for r in results] == list(range(10))
    assert sorted(chain.chunk_sizes) == [1, 3, 3, 3]
    assert chain.max_active == 2
    assert chain.seen_call_data == {GET_RESERVES_SELECTOR}


async def test_aggregate3_reports_failed_and_empty_calls(chain: FakeChain) -> None:
    chain.contracts[addr(1)] = b"ok"
    chain.contracts[addr(2)] = FAIL
    calls = [Call(addr(i), b"\x01\x02\x03\x04") for i in (1, 2, 3)]

    async with open_web3(chain.url) as w3:
        results = await aggregate3(w3, calls)

    assert results == [
        CallResult(True, b"ok"),
        CallResult(False, b""),
        CallResult(True, b""),
    ]


async def test_aggregate3_without_calls_makes_no_request(chain: FakeChain) -> None:
    async with open_web3(chain.url) as w3:
        assert await aggregate3(w3, []) == []
    assert chain.chunk_sizes == []


@pytest.mark.parametrize("kwargs", [{"chunk_size": 0}, {"max_concurrent": 0}])
async def test_aggregate3_rejects_invalid_limits(chain: FakeChain, kwargs: dict[str, int]) -> None:
    async with open_web3(chain.url) as w3:
        with pytest.raises(ValueError):
            await aggregate3(w3, [Call(addr(1), b"")], **kwargs)


async def test_aggregate3_propagates_rpc_errors(chain: FakeChain) -> None:
    chain.fail_requests = True
    async with open_web3(chain.url) as w3:
        with pytest.raises(Web3Exception):
            await aggregate3(w3, [Call(addr(1), b"")])


async def test_fetch_reserves_many_returns_none_for_unreadable_pairs(chain: FakeChain) -> None:
    chain.contracts[addr(1)] = reserves_data(10, 20)
    chain.contracts[addr(2)] = FAIL
    chain.contracts[addr(4)] = b"\x00" * 10

    async with open_web3(chain.url) as w3:
        reserves = await fetch_reserves_many(w3, [addr(1), addr(2), addr(3), addr(4)])

    assert reserves == [(10, 20), None, None, None]


@pytest.fixture
def tokens() -> tuple[Token, Token, Token]:
    return (
        Token(chain_id=1, address=WETH, symbol="WETH", decimals=18),
        Token(chain_id=1, address=USDC, symbol="USDC", decimals=6),
        Token(chain_id=1, address=DAI, symbol="DAI", decimals=18),
    )


async def test_quote_many_skips_broken_empty_and_unrelated_pools(
    chain: FakeChain, tokens: tuple[Token, Token, Token], caplog: pytest.LogCaptureFixture
) -> None:
    weth, usdc, dai = tokens
    good1, broken, empty, unrelated, good2 = (addr(i) for i in (1, 2, 3, 4, 5))
    chain.contracts[good1] = reserves_data(2_000_000 * 10**6, 1_000 * 10**18)
    chain.contracts[broken] = FAIL
    chain.contracts[empty] = reserves_data(0, 0)
    chain.contracts[good2] = reserves_data(4_000_000 * 10**6, 2_000 * 10**18)
    pools = [
        Pool(address=good1, dex_id=1, tokens=(weth, usdc), fee=3_000),
        Pool(address=broken, dex_id=1, tokens=(weth, usdc), fee=3_000),
        Pool(address=empty, dex_id=1, tokens=(weth, usdc), fee=3_000),
        Pool(address=unrelated, dex_id=1, tokens=(weth, dai), fee=3_000),
        Pool(address=good2, dex_id=3, tokens=(weth, usdc), fee=2_500),
    ]

    with caplog.at_level(logging.INFO, logger="pool_prices.dex.v2"):
        async with open_web3(chain.url) as w3:
            quotes = await v2.quote_many(w3, pools, weth, usdc, 10**18)

    assert [q.pool.address for q in quotes] == [good1, good2]
    assert quotes[0].amount_out == get_amount_out(10**18, 1_000 * 10**18, 2_000_000 * 10**6, 3_000)
    assert quotes[1].amount_out == get_amount_out(10**18, 2_000 * 10**18, 4_000_000 * 10**6, 2_500)
    assert chain.chunk_sizes == [4]
    assert any("could not read reserves" in m and broken in m for m in caplog.messages)
    assert any("empty reserves" in m and empty in m for m in caplog.messages)


async def test_quote_many_with_no_matching_pools_makes_no_request(
    chain: FakeChain, tokens: tuple[Token, Token, Token]
) -> None:
    weth, usdc, dai = tokens
    pools = [Pool(address=addr(1), dex_id=1, tokens=(weth, dai), fee=3_000)]

    async with open_web3(chain.url) as w3:
        assert await v2.quote_many(w3, pools, weth, usdc, 10**18) == []
    assert chain.chunk_sizes == []


async def test_aggregate3_retries_transient_http_errors(chain: FakeChain) -> None:
    chain.contracts[addr(1)] = b"ok"
    chain.http_failures = [503, 429]
    async with open_web3(chain.url) as w3:
        results = await aggregate3(w3, [Call(addr(1), b"\x01\x02\x03\x04")], retry_attempts=3)
    assert results == [CallResult(True, b"ok")]


async def test_aggregate3_gives_up_after_retry_attempts(chain: FakeChain) -> None:
    chain.http_failures = [503, 503, 503]
    async with open_web3(chain.url) as w3:
        with pytest.raises(Exception, match="503"):
            await aggregate3(w3, [Call(addr(1), b"")], retry_attempts=2)
    assert chain.http_failures == [503]


async def test_aggregate3_does_not_retry_client_errors(chain: FakeChain) -> None:
    chain.http_failures = [401, 401]
    async with open_web3(chain.url) as w3:
        with pytest.raises(Exception, match="401"):
            await aggregate3(w3, [Call(addr(1), b"")], retry_attempts=3)
    assert chain.http_failures == [401]
