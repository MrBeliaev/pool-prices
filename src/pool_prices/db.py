import json
from collections.abc import AsyncGenerator, Iterable, Sequence
from contextlib import asynccontextmanager
from typing import Any, Protocol

import asyncpg

from pool_prices.models import Dex, Pool

GET_DEX_SQL = """
    SELECT id, name, protocol, chain_id, factory_address, quoter_address, fee
    FROM dexes
    WHERE id = $1
"""

FIND_POOLS_SQL = """
    SELECT
    p.address,
    p.dex_id,
    p.fee,
    json_agg(
        json_build_object(
            'chain_id', t.chain_id,
            'address',  t.address,
            'symbol',   t.symbol,
            'decimals', t.decimals
        )
        ORDER BY pt.position
    ) AS tokens
    FROM pools p
    JOIN pool_tokens pt ON pt.pool_id = p.id
    JOIN tokens t       ON t.id = pt.token_id
    WHERE p.id IN (
    SELECT pt2.pool_id
    FROM pool_tokens pt2
    JOIN tokens t2 ON t2.id = pt2.token_id
    WHERE t2.chain_id = $1 AND t2.address = ANY($2::text[])
    GROUP BY pt2.pool_id
    HAVING count(DISTINCT t2.address) = 2
    )
    GROUP BY p.id
    ORDER BY p.id
"""


class Row(Protocol):
    def keys(self) -> Iterable[str]: ...

    def __getitem__(self, key: str, /) -> Any: ...


class SupportsQueries(Protocol):
    async def fetch(self, query: str, *args: Any) -> Sequence[Row]: ...

    async def fetchrow(self, query: str, *args: Any) -> Row | None: ...


def dex_from_row(row: Row) -> Dex:
    return Dex.model_validate(dict(row))


def pool_from_row(row: Row) -> Pool:
    data = dict(row)

    if isinstance(data["tokens"], str):
        data["tokens"] = json.loads(data["tokens"])

    return Pool.model_validate(data)


class DB:
    def __init__(self, pool: SupportsQueries) -> None:
        self._pool = pool

    async def get_dex(self, dex_id: int) -> Dex | None:
        row = await self._pool.fetchrow(GET_DEX_SQL, dex_id)
        return dex_from_row(row) if row is not None else None

    async def find_pools(self, chain_id: int, token_a: str, token_b: str) -> list[Pool]:
        addresses = [token_a.lower(), token_b.lower()]
        rows = await self._pool.fetch(FIND_POOLS_SQL, chain_id, addresses)
        return [pool_from_row(row) for row in rows]


@asynccontextmanager
async def open_db(dsn: str, *, min_size: int = 1, max_size: int = 10) -> AsyncGenerator[DB]:

    pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)
    if pool is None:
        raise RuntimeError("could not create connection pool")
    try:
        yield DB(pool)
    finally:
        await pool.close()
