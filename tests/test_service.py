import logging
from collections.abc import Sequence
from typing import Any

import pytest

from pool_prices.dex.v2 import quote_from_reserves
from pool_prices.errors import NoRouteError, QuotersFailedError, UnsupportedPairError
from pool_prices.models import Dex, DexProtocol, Pool, Quote, Token
from pool_prices.service import PriceService, Quoter, ranking_key

WETH_ADDR = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC_ADDR = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
WETH = Token(chain_id=1, address=WETH_ADDR, symbol="WETH", decimals=18)
USDC = Token(chain_id=1, address=USDC_ADDR, symbol="USDC", decimals=6)


def addr(i: int) -> str:
    return f"0x{i:040x}"


def make_pool(i: int, dex_id: int = 1, fee: int = 3_000) -> Pool:
    return Pool(address=addr(i), dex_id=dex_id, tokens=(WETH, USDC), fee=fee)


def make_dex(dex_id: int, protocol: DexProtocol) -> Dex:
    return Dex(
        id=dex_id,
        name=f"dex{dex_id}",
        protocol=protocol,
        chain_id=1,
        factory_address=addr(900 + dex_id),
        fee=3_000 if protocol == "uniswap_v2" else None,
    )


class FakeDb:
    def __init__(self, pools: list[Pool], dexes: list[Dex]) -> None:
        self.pools = pools
        self.dexes = {d.id: d for d in dexes}
        self.find_calls: list[tuple[int, str, str]] = []

    async def find_pools(self, chain_id: int, token_a: str, token_b: str) -> list[Pool]:
        self.find_calls.append((chain_id, token_a, token_b))
        return self.pools

    async def get_dex(self, dex_id: int) -> Dex | None:
        return self.dexes.get(dex_id)


def fake_quoter(reserves_by_pool: dict[str, tuple[int, int]], seen: list[Sequence[Pool]]) -> Quoter:
    async def quoter(
        w3: Any, pools: Sequence[Pool], token_in: Token, token_out: Token, amount_in: int
    ) -> list[Quote]:
        seen.append(pools)
        return [
            quote_from_reserves(p, token_in, token_out, amount_in, reserves_by_pool[p.address])
            for p in pools
        ]

    return quoter


def failing_quoter(error: Exception) -> Quoter:
    async def quoter(
        w3: Any, pools: Sequence[Pool], token_in: Token, token_out: Token, amount_in: int
    ) -> list[Quote]:
        raise error

    return quoter


RICH = (4_000_000 * 10**6, 2_000 * 10**18)  # 2000 USDC = 1 WETH
POOR = (1_000_000 * 10**6, 1_000 * 10**18)  # 1000 USDC = 1 WETH


async def test_best_quote_picks_highest_amount_out_and_sorts_the_rest() -> None:
    pools = [make_pool(1), make_pool(2), make_pool(3)]
    reserves = {addr(1): POOR, addr(2): RICH, addr(3): (3_000_000 * 10**6, 2_000 * 10**18)}
    seen: list[Sequence[Pool]] = []
    service = PriceService(
        FakeDb(pools, [make_dex(1, "uniswap_v2")]),
        w3=None,
        quoters={"uniswap_v2": fake_quoter(reserves, seen)},
    )

    result = await service.best_quote(1, WETH_ADDR.lower(), USDC_ADDR, 10**18)

    assert result.best.pool.address == addr(2)
    assert [q.pool.address for q in result.alternatives] == [addr(3), addr(1)]
    amounts = [q.amount_out for q in result.all]
    assert amounts == sorted(amounts, reverse=True)
    assert len(seen) == 1 and len(seen[0]) == 3


async def test_pools_are_routed_to_quoter_of_their_protocol() -> None:
    pools = [make_pool(1, dex_id=1), make_pool(2, dex_id=2), make_pool(3, dex_id=3)]
    dexes = [make_dex(1, "uniswap_v2"), make_dex(2, "uniswap_v3"), make_dex(3, "uniswap_v2")]
    seen: list[Sequence[Pool]] = []
    reserves = {addr(1): POOR, addr(3): RICH}
    service = PriceService(
        FakeDb(pools, dexes),
        w3=None,
        quoters={"uniswap_v2": fake_quoter(reserves, seen)},
    )

    quotes = await service.get_quotes(1, WETH_ADDR, USDC_ADDR, 10**18)

    assert [p.address for p in seen[0]] == [addr(1), addr(3)]
    assert {q.pool.address for q in quotes} == {addr(1), addr(3)}


async def test_unknown_dex_is_skipped_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    pools = [make_pool(1, dex_id=1), make_pool(2, dex_id=99)]
    service = PriceService(
        FakeDb(pools, [make_dex(1, "uniswap_v2")]),
        w3=None,
        quoters={"uniswap_v2": fake_quoter({addr(1): RICH}, [])},
    )
    with caplog.at_level(logging.WARNING, logger="pool_prices.service"):
        quotes = await service.get_quotes(1, WETH_ADDR, USDC_ADDR, 10**18)

    assert [q.pool.address for q in quotes] == [addr(1)]
    assert any("dex 99 not found" in m for m in caplog.messages)


async def test_failure_of_one_protocol_does_not_hide_other_results(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pools = [make_pool(1, dex_id=1), make_pool(2, dex_id=2)]
    dexes = [make_dex(1, "uniswap_v2"), make_dex(2, "uniswap_v3")]
    service = PriceService(
        FakeDb(pools, dexes),
        w3=None,
        quoters={
            "uniswap_v2": fake_quoter({addr(1): RICH}, []),
            "uniswap_v3": failing_quoter(RuntimeError("quoter reverted")),
        },
    )
    with caplog.at_level(logging.ERROR, logger="pool_prices.service"):
        quotes = await service.get_quotes(1, WETH_ADDR, USDC_ADDR, 10**18)

    assert [q.pool.address for q in quotes] == [addr(1)]
    assert any("uniswap_v3" in m and "quoter reverted" in m for m in caplog.messages)


async def test_all_quoters_failing_raises_with_original_cause() -> None:
    boom = ConnectionError("rpc down")
    service = PriceService(
        FakeDb([make_pool(1)], [make_dex(1, "uniswap_v2")]),
        w3=None,
        quoters={"uniswap_v2": failing_quoter(boom)},
    )
    with pytest.raises(QuotersFailedError) as info:
        await service.get_quotes(1, WETH_ADDR, USDC_ADDR, 10**18)
    assert info.value.__cause__ is boom


async def test_no_pools_and_no_liquidity_raise_no_route() -> None:
    empty = PriceService(FakeDb([], []), w3=None)  # type: ignore[arg-type]
    with pytest.raises(NoRouteError):
        await empty.get_quotes(1, WETH_ADDR, USDC_ADDR, 10**18)

    no_liquidity = PriceService(
        FakeDb([make_pool(1)], [make_dex(1, "uniswap_v2")]),
        w3=None,
        quoters={"uniswap_v2": _empty_quoter()},
    )
    with pytest.raises(NoRouteError):
        await no_liquidity.get_quotes(1, WETH_ADDR, USDC_ADDR, 10**18)


def _empty_quoter() -> Quoter:
    async def quoter(
        w3: Any, pools: Sequence[Pool], token_in: Token, token_out: Token, amount_in: int
    ) -> list[Quote]:
        return []

    return quoter


async def test_input_validation_happens_before_any_database_call() -> None:
    db = FakeDb([make_pool(1)], [make_dex(1, "uniswap_v2")])
    service = PriceService(db, w3=None)

    with pytest.raises(ValueError):
        await service.get_quotes(1, WETH_ADDR, USDC_ADDR, 0)
    with pytest.raises(UnsupportedPairError):
        await service.get_quotes(1, WETH_ADDR, WETH_ADDR.lower(), 10**18)
    assert db.find_calls == []


def test_ranking_is_deterministic_on_ties() -> None:
    quote_a = quote_from_reserves(make_pool(1), WETH, USDC, 10**18, RICH)
    quote_b = quote_from_reserves(make_pool(2), WETH, USDC, 10**18, RICH)
    assert quote_a.amount_out == quote_b.amount_out
    assert sorted([quote_b, quote_a], key=ranking_key) == [quote_a, quote_b]
    assert sorted([quote_a, quote_b], key=ranking_key) == [quote_a, quote_b]
