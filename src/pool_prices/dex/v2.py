from typing import Any

from web3 import AsyncWeb3

from pool_prices.dex.abis import UNISWAP_V2_PAIR_ABI
from pool_prices.errors import InsufficientLiquidityError, UnsupportedPairError
from pool_prices.models import Pool, Quote, Token

FEE_DENOMINATOR = 1000000


async def fetch_reserves(w3: AsyncWeb3[Any], pair_address: str) -> tuple[int, int]:
    contract = w3.eth.contract(
        address=AsyncWeb3.to_checksum_address(pair_address), abi=UNISWAP_V2_PAIR_ABI
    )

    reserve0, reserve1, _ = await contract.functions.getReserves().call()

    return int(reserve0), int(reserve1)


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
