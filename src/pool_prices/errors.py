class PoolPricesError(Exception):
    """Base exception for the service."""


class UnsupportedPairError(PoolPricesError):
    """The pool can not swap token_in for token_out or has an unsupported structure."""


class InsufficientLiquidityError(PoolPricesError):
    """The pool has insufficient liquidity to calculate a quote."""


class NoRouteError(PoolPricesError):
    """No quotes are available for the pair: no pools,
    insufficient liquidity, or unsupported protocol."""


class QuotersFailedError(PoolPricesError):
    """All quote attempts failed, for example, due to an unavailable RPC endpoint."""
