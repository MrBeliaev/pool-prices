import json
from collections.abc import Mapping, Sequence
from typing import Any

from pool_prices.db import DB, FIND_POOLS_SQL, GET_DEX_SQL

WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
POOL = "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc"


class FakePool:
    def __init__(
        self,
        row: Mapping[str, Any] | None = None,
        rows: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self._row = row
        self._rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, query: str, *args: Any) -> Mapping[str, Any] | None:
        self.calls.append((query, args))
        return self._row

    async def fetch(self, query: str, *args: Any) -> Sequence[Mapping[str, Any]]:
        self.calls.append((query, args))
        return self._rows


def _token_json(address: str, symbol: str, decimals: int) -> dict[str, Any]:
    return {"chain_id": 1, "address": address, "symbol": symbol, "decimals": decimals}


async def test_get_dex_maps_row_to_model() -> None:
    row = {
        "id": 2,
        "name": "Uniswap V2",
        "protocol": "uniswap_v2",
        "chain_id": 1,
        "factory_address": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        "quoter_address": None,
        "fee": 3000,
    }
    fake = FakePool(row=row)

    dex = await DB(fake).get_dex(2)

    assert dex is not None
    assert dex.protocol == "uniswap_v2"
    assert dex.factory_address == row["factory_address"].lower()
    assert fake.calls == [(GET_DEX_SQL, (2,))]


async def test_get_dex_returns_none_when_missing() -> None:
    assert await DB(FakePool(row=None)).get_dex(999) is None


async def test_find_pools_parses_json_tokens_and_lowercases_input() -> None:
    tokens = [_token_json(WETH, "WETH", 18), _token_json(USDC, "USDC", 6)]
    row = {"address": POOL, "dex_id": 2, "fee": 3000, "tokens": json.dumps(tokens)}
    fake = FakePool(rows=[row])

    pools = await DB(fake).find_pools(1, WETH.upper().replace("0X", "0x"), USDC)

    assert len(pools) == 1
    assert [t.symbol for t in pools[0].tokens] == ["WETH", "USDC"]
    assert pools[0].dex_id == 2
    query, args = fake.calls[0]
    assert query == FIND_POOLS_SQL
    assert args == (1, [WETH, USDC])


async def test_find_pools_returns_empty_list_when_nothing_found() -> None:
    assert await DB(FakePool(rows=[])).find_pools(1, WETH, USDC) == []
