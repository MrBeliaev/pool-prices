class PoolPricesError(Exception):
    """Base exception for the service."""


class UnsupportedPairError(PoolPricesError):
    """The pool cannot swap token_in for token_out or has an unsupported structure."""


class InsufficientLiquidityError(PoolPricesError):
    """The pool has insufficient liquidity to calculate a quote."""
