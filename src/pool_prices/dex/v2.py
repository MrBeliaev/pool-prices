import logging
from collections.abc import Sequence
from typing import Any

from eth_abi.abi import decode
from web3 import AsyncWeb3

from pool_prices.chain.multicall import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MAX_CONCURRENT,
    MULTICALL3_ADDRESS,
    Call,
    aggregate3,
    selector,
)
from pool_prices.dex.abis import UNISWAP_V2_PAIR_ABI
from pool_prices.errors import InsufficientLiquidityError, UnsupportedPairError
from pool_prices.models import Pool, Quote, Token

logger = logging.getLogger(__name__)

FEE_DENOMINATOR = 1000000
GET_RESERVES_SELECTOR = selector("getReserves()")
RESERVES_RESPONSE_SIZE = 96


async def fetch_reserves(w3: AsyncWeb3[Any], pair_address: str) -> tuple[int, int]:
    contract = w3.eth.contract(
        address=AsyncWeb3.to_checksum_address(pair_address), abi=UNISWAP_V2_PAIR_ABI
    )

    reserve0, reserve1, _ = await contract.functions.getReserves().call()

    return int(reserve0), int(reserve1)


def decode_reserves(return_data: bytes) -> tuple[int, int] | None:
    if len(return_data) < RESERVES_RESPONSE_SIZE:
        return None
    reserve0, reserve1, _ = decode(["uint112", "uint112", "uint32"], return_data)
    return int(reserve0), int(reserve1)


async def fetch_reserves_many(
    w3: AsyncWeb3[Any],
    pair_addresses: Sequence[str],
    *,
    multicall_address: str = MULTICALL3_ADDRESS,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> list[tuple[int, int] | None]:
    calls = [Call(target=address, call_data=GET_RESERVES_SELECTOR) for address in pair_addresses]
    results = await aggregate3(
        w3,
        calls,
        multicall_address=multicall_address,
        chunk_size=chunk_size,
        max_concurrent=max_concurrent,
    )

    return [decode_reserves(r.return_data) if r.success else None for r in results]


def get_amount_out(amount_in: int, reserve_in: int, reserve_out: int, fee: int) -> int:
    if not 0 <= fee < FEE_DENOMINATOR:
        raise ValueError(f"fee must be in [0, {FEE_DENOMINATOR}), got {fee}]")
    if amount_in <= 0:
        raise ValueError("amount_in must be positive")
    if reserve_in <= 0 or reserve_out <= 0:
        raise InsufficientLiquidityError("pool reserves are emoty")

    amount_in_with_fee = amount_in * (FEE_DENOMINATOR - fee)
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in * FEE_DENOMINATOR + amount_in_with_fee

    return numerator // denominator


def quote_from_reserves(
    pool: Pool, token_in: Token, token_out: Token, amount_in: int, reserves: tuple[int, int]
) -> Quote:
    if len(pool.tokens) != 2 or not pool.suppports_pair(token_in, token_out):
        raise UnsupportedPairError(f"pool {pool.address} cannot swap these tokens")

    token0, token1 = sorted(pool.tokens, key=lambda token: token.address)
    reserve_by_token = {token0: reserves[0], token1: reserves[1]}
    reserve_out = reserve_by_token[token_out]
    amount_out = get_amount_out(amount_in, reserve_by_token[token_in], reserve_out, pool.fee)

    return Quote(
        pool=pool,
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in,
        amount_out=amount_out,
        pool_liquidity_out=reserve_out,
    )


async def quote(
    w3: AsyncWeb3[Any],
    pool: Pool,
    token_in: Token,
    token_out: Token,
    amount_in: int,
) -> Quote:
    reserves = await fetch_reserves(w3, pool.address)
    return quote_from_reserves(pool, token_in, token_out, amount_in, reserves)


async def quote_many(
    w3: AsyncWeb3[Any],
    pools: Sequence[Pool],
    token_in: Token,
    token_out: Token,
    amount_in: int,
    *,
    multiall_address: str = MULTICALL3_ADDRESS,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> list[Quote]:
    candidates = [
        pool for pool in pools if len(pool.tokens) == 2 and pool.suppports_pair(token_in, token_out)
    ]

    reserves = await fetch_reserves_many(
        w3,
        [pool.address for pool in candidates],
        multicall_address=multiall_address,
        chunk_size=chunk_size,
        max_concurrent=max_concurrent,
    )

    quotes: list[Quote] = []

    for pool, pool_reserves in zip(candidates, reserves, strict=True):
        if pool_reserves is None:
            logger.warning("pool %s: could not read reserves, skipped", pool.address)
            continue
        try:
            quotes.append(quote_from_reserves(pool, token_in, token_out, amount_in, pool_reserves))
        except InsufficientLiquidityError:
            logger.info("pool %s: empty reserves, skipped", pool.address)

    return quotes
