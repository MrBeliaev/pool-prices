import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from pool_prices.db import DB

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(DSN is None, reason="TEST_DATABASE_URL is not set")

SCHEMA_SQL = (Path(__file__).parent.parent / "sql" / "schema.sql").read_text()

WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"


@pytest.fixture
async def db() -> AsyncIterator[DB]:
    assert DSN is not None
    schema = f"pp_test_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(DSN)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    pool = await asyncpg.create_pool(DSN, server_settings={"search_path": schema})
    assert pool is not None
    try:
        await pool.execute(SCHEMA_SQL)
        await _seed(pool)
        yield DB(pool)
    finally:
        await pool.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


async def _seed(pool: asyncpg.Pool) -> None:
    await pool.execute(
        """INSERT INTO dexes (id, name, protocol, chain_id, factory_address, quoter_address)
           VALUES (1, 'Uniswap V2', 'uniswap_v2', 1, '0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f', NULL),
                  (2, 'Uniswap V3', 'uniswap_v3', 1, '0x1f98431c8ad98523631ae4a59f267346ea31f984',
                   '0x61ffe014ba17989e743c5f6cb21bf9697530b21e')"""  # noqa: E501
    )
    await pool.execute(
        """INSERT INTO tokens (id, chain_id, address, symbol, decimals)
           VALUES (1, 1, $1, 'WETH', 18), (2, 1, $2, 'USDC', 6), (3, 1, $3, 'DAI', 18)""",
        WETH,
        USDC,
        DAI,
    )

    await pool.execute(
        """INSERT INTO pools (id, dex_id, address, fee) VALUES
           (1, 1, '0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc', NULL),
           (2, 2, '0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640', 500),
           (3, 1, '0x0000000000000000000000000000000000000003', NULL),
           (4, 1, '0x0000000000000000000000000000000000000004', NULL)"""
    )
    await pool.execute(
        """INSERT INTO pool_tokens (pool_id, token_id, position) VALUES
           (1, 1, 0), (1, 2, 1),
           (2, 2, 0), (2, 1, 1),
           (3, 1, 0), (3, 2, 1), (3, 3, 2),
           (4, 1, 0), (4, 3, 1)"""
    )


async def test_get_dex(db: DB) -> None:
    dex = await db.get_dex(2)
    assert dex is not None
    assert dex.protocol == "uniswap_v3"
    assert dex.quoter_address == "0x61ffe014ba17989e743c5f6cb21bf9697530b21e"
    assert await db.get_dex(999) is None


async def test_find_pools_returns_only_pools_with_both_tokens(db: DB) -> None:
    pools = await db.find_pools(1, WETH, USDC)

    assert [p.address[-4:] for p in pools] == ["c9dc", "5640", "0003"]  # пул 4 не попал
    assert [p.fee for p in pools] == [None, 500, None]


async def test_find_pools_keeps_token_order_and_supports_three_tokens(db: DB) -> None:
    by_dex_id_and_len = {(p.dex_id, len(p.tokens)): p for p in await db.find_pools(1, WETH, USDC)}

    v3 = by_dex_id_and_len[(2, 2)]
    assert [t.symbol for t in v3.tokens] == ["USDC", "WETH"]
    assert (1, 3) in by_dex_id_and_len


async def test_find_pools_accepts_checksum_input(db: DB) -> None:
    mixed_case = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    assert len(await db.find_pools(1, mixed_case, USDC)) == 3


async def test_find_pools_other_chain_is_empty(db: DB) -> None:
    assert await db.find_pools(56, WETH, USDC) == []
