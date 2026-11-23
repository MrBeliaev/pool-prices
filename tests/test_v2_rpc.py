from collections.abc import AsyncIterator
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from web3 import AsyncWeb3

from pool_prices.dex.v2 import fetch_reserves, get_amount_out, quote
from pool_prices.models import Pool, Token
from pool_prices.node_client import open_web3

WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
PAIR = "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"
GET_RESERVES_SELECTOR = "0x0902f1ac"

RESERVE0 = 2_000_000 * 10**6
RESERVE1 = 1_000 * 10**18


def _word(value: int) -> str:
    return f"{value:064x}"


@pytest.fixture
async def rpc_url() -> AsyncIterator[str]:
    received: list[dict[str, Any]] = []

    async def handle(request: web.Request) -> web.Response:
        body = await request.json()
        received.append(body)
        method = body["method"]
        if method == "eth_chainId":
            result: Any = "0x1"
        elif method == "eth_call":
            call = body["params"][0]
            assert call["to"].lower() == PAIR.lower()
            assert call["data" if "data" in call else "input"].startswith(GET_RESERVES_SELECTOR)
            result = "0x" + _word(RESERVE0) + _word(RESERVE1) + _word(1_700_000_000)
        else:
            return web.json_response(
                {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": method}}
            )
        return web.json_response({"jsonrpc": "2.0", "id": body["id"], "result": result})

    app = web.Application()
    app.router.add_post("/", handle)
    server = TestServer(app)
    await server.start_server()
    try:
        yield str(server.make_url("/"))
    finally:
        await server.close()


async def test_fetch_reserves_through_real_web3(rpc_url: str) -> None:
    async with open_web3(rpc_url, timeout_seconds=5) as w3:
        assert await fetch_reserves(w3, PAIR.lower()) == (RESERVE0, RESERVE1)


async def test_quote_end_to_end_through_real_web3(rpc_url: str) -> None:
    weth = Token(chain_id=1, address=WETH, symbol="WETH", decimals=18)
    usdc = Token(chain_id=1, address=USDC, symbol="USDC", decimals=6)
    pool = Pool(address=PAIR, dex_id=1, tokens=(weth, usdc), fee=3000)

    async with open_web3(rpc_url, timeout_seconds=5) as w3:
        result = await quote(w3, pool, weth, usdc, 10**18)

    assert result.amount_out == get_amount_out(10**18, RESERVE1, RESERVE0, pool.fee)
    assert result.pool_liquidity_out == RESERVE0
    assert AsyncWeb3.is_checksum_address(AsyncWeb3.to_checksum_address(pool.address))
