from collections.abc import AsyncIterator

import httpx
import pytest

from pool_prices.api import create_app
from pool_prices.dex.v2 import quote_from_reserves
from pool_prices.errors import (
    NoRouteError,
    PoolPricesError,
    QuotersFailedError,
    UnsupportedPairError,
)
from pool_prices.models import Pool, Token
from pool_prices.service import PriceResult

WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
PAIR = "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"
T_WETH = Token(chain_id=1, address=WETH, symbol="WETH", decimals=18)
T_USDC = Token(chain_id=1, address=USDC, symbol="USDC", decimals=6)
PARAMS = {"chain_id": "1", "token_in": WETH, "token_out": USDC, "amount_in": str(10**18)}


class FakeProvider:
    def __init__(self, error: PoolPricesError | None = None) -> None:
        self.error = error
        self.calls: list[tuple[int, str, str, int]] = []

    async def best_quote(
        self, chain_id: int, token_in: str, token_out: str, amount_in: int
    ) -> PriceResult:
        self.calls.append((chain_id, token_in, token_out, amount_in))
        if self.error is not None:
            raise self.error
        pool = Pool(address=PAIR, dex_id=1, tokens=(T_WETH, T_USDC), fee=3_000)
        best = quote_from_reserves(
            pool, T_WETH, T_USDC, amount_in, (4_000_000 * 10**6, 2_000 * 10**18)
        )
        return PriceResult(best=best, alternatives=())


async def client_for(provider: FakeProvider) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(provider)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


@pytest.fixture
async def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
async def client(provider: FakeProvider) -> AsyncIterator[httpx.AsyncClient]:
    async for c in client_for(provider):
        yield c


async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


async def test_quote_returns_amounts_as_strings(
    client: httpx.AsyncClient, provider: FakeProvider
) -> None:
    response = await client.get("/v1/quote", params=PARAMS)

    assert response.status_code == 200
    body = response.json()
    assert body["alternatives"] == []
    assert body["best"]["amount_in"] == str(10**18)
    assert isinstance(body["best"]["amount_out"], str)
    assert int(body["best"]["amount_out"]) > 0
    assert provider.calls == [(1, WETH, USDC, 10**18)]


@pytest.mark.parametrize(
    "override",
    [
        {"amount_in": "0"},
        {"amount_in": "-5"},
        {"amount_in": "1.5"},
        {"amount_in": "1" + "0" * 78},
        {"token_in": "0x123"},
        {"token_out": "USDC"},
        {"chain_id": "0"},
        {"chain_id": "abc"},
    ],
)
async def test_invalid_input_is_rejected_before_service(
    client: httpx.AsyncClient, provider: FakeProvider, override: dict[str, str]
) -> None:
    response = await client.get("/v1/quote", params={**PARAMS, **override})
    assert response.status_code == 422
    assert provider.calls == []


@pytest.mark.parametrize(
    ("error", "status", "name"),
    [
        (NoRouteError("no pools"), 404, "no_route"),
        (UnsupportedPairError("same token"), 422, "unsupported_pair"),
        (QuotersFailedError("rpc down"), 502, "upstream_failed"),
    ],
)
async def test_domain_errors_map_to_http_codes(
    error: PoolPricesError, status: int, name: str
) -> None:
    async for c in client_for(FakeProvider(error)):
        response = await c.get("/v1/quote", params=PARAMS)
        assert response.status_code == status
        assert response.json() == {"error": name, "detail": str(error)}
