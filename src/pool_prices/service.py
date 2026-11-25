import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from web3 import AsyncWeb3

from pool_prices.dex.v2 import quote_many
from pool_prices.errors import NoRouteError, QuotersFailedError, UnsupportedPairError
from pool_prices.models import Dex, DexProtocol, Pool, Quote, Token

logger = logging.getLogger(__name__)

Quoter = Callable[[AsyncWeb3[Any], Sequence[Pool], Token, Token, int], Awaitable[list[Quote]]]

QUOTERS: Mapping[DexProtocol, Quoter] = {"uniswap_v2": quote_many}


class PoolSource(Protocol):
    async def find_pools(self, chaon_id: int, token_a: str, token_b: str) -> list[Pool]: ...

    async def get_dex(self, dex_id: int) -> Dex | None: ...


@dataclass(frozen=True, slots=True)
class PriceResult:
    best: Quote
    alternatives: tuple[Quote, ...]

    @property
    def all(self) -> tuple[Quote, ...]:
        return (self.best, *self.alternatives)


def ranking_key(quote: Quote) -> tuple[int, int, str]:
    return (-quote.amount_out, -quote.pool_liquidity_out, quote.pool.address)


def find_token(pools: Sequence[Pool], address: str) -> Token:
    wanted = address.lower()
    for pool in pools:
        for token in pool.tokens:
            if token.address == wanted:
                return token
    raise NoRouteError(f"token {address} not found")


class PriceService:
    def __init__(
        self,
        db: PoolSource,
        w3: AsyncWeb3[Any],
        quoters: Mapping[DexProtocol, Quoter] = QUOTERS,
        total_timeout: float = 10.0,
    ) -> None:
        self._db = db
        self._w3 = w3
        self._quoters = quoters
        self._total_timeout = total_timeout

    async def get_quotes(
        self, chain_id: int, token_in: str, token_out: str, amount_in: int
    ) -> list[Quote]:
        if amount_in <= 0:
            raise ValueError("amount_in must br positive")
        if token_in.lower() == token_out.lower():
            raise UnsupportedPairError("token_in and token_out must be different")

        pools = await self._db.find_pools(chain_id, token_in, token_out)
        if not pools:
            raise NoRouteError(f"no pools for {token_in} / {token_out} on chain {chain_id}")

        token_in_obj = find_token(pools, token_in)
        token_out_obj = find_token(pools, token_out)

        by_protocol = await self._group_by_protocol(pools)
        protocols = list(by_protocol)
        try:
            async with asyncio.timeout(self._total_timeout):
                outcomes = await asyncio.gather(
                    *(
                        self._quoters[protocol](
                            self._w3, by_protocol[protocol], token_in_obj, token_out_obj, amount_in
                        )
                        for protocol in protocols
                    ),
                    return_exceptions=True,
                )
        except TimeoutError as error:
            raise QuotersFailedError(f"timed out after {self._total_timeout}s") from error

        quotes: list[Quote] = []
        errors: list[Exception] = []
        for protocol, outcome in zip(protocols, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                if not isinstance(outcome, Exception):
                    raise outcome
                logger.error("quoter %s failed: %r", protocol, outcome)
                errors.append(outcome)
            else:
                quotes.extend(outcome)

        if not quotes:
            if errors:
                raise QuotersFailedError("all quoters failed") from errors[0]
            raise NoRouteError(f"no liquidity for {token_in} -> {token_out}")

        return sorted(quotes, key=ranking_key)

    async def best_quote(
        self, chain_id: int, token_in: str, token_out: str, amount_in: int
    ) -> PriceResult:
        quotes = await self.get_quotes(chain_id, token_in, token_out, amount_in)

        return PriceResult(best=quotes[0], alternatives=tuple(quotes[1:]))

    async def _group_by_protocol(self, pools: Sequence[Pool]) -> dict[DexProtocol, list[Pool]]:
        dex_ids = sorted({pool.dex_id for pool in pools})
        dexes = dict(
            zip(dex_ids, await asyncio.gather(*(self._db.get_dex(i) for i in dex_ids)), strict=True)
        )
        groups: dict[DexProtocol, list[Pool]] = defaultdict(list)

        for pool in pools:
            dex = dexes[pool.dex_id]
            if dex is None:
                logger.warning("pool %s: dex %s not found, skipped", pool.address, pool.dex_id)
            elif dex.protocol not in self._quoters:
                logger.info("pool %s: protocol %s not supported yet", pool.address, dex.protocol)
            else:
                groups[dex.protocol].append(pool)

        return dict(groups)
