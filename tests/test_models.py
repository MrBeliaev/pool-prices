import json

import pytest
from pydantic import ValidationError

from pool_prices.models import Dex, Pool, Quote, Token

WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
POOL = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"
V3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
V3_QUOTER = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
V2_FACTORY = "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"


@pytest.fixture
def weth() -> Token:
    return Token(chain_id=1, address=WETH, symbol="WETH", decimals=18)


@pytest.fixture
def usdc() -> Token:
    return Token(chain_id=1, address=USDC, symbol="USDC", decimals=6)


@pytest.fixture
def dai() -> Token:
    return Token(chain_id=1, address=DAI, symbol="DAI", decimals=18)


# Token


def test_token_address_is_normalized_to_lowercase(weth: Token) -> None:
    assert weth.address == WETH.lower()


def test_token_rejects_invalid_address() -> None:
    with pytest.raises(ValidationError):
        Token(chain_id=1, address="0x123", symbol="TT", decimals=18)


def test_token_immutable(weth: Token) -> None:
    with pytest.raises(ValidationError):
        weth.symbol = "NEW"


def test_token_is_hashable(weth: Token) -> None:
    assert weth in {weth}


# Dex


def test_dex_v3_has_quoter_and_normalized_addresses() -> None:
    dex = Dex(
        id=1,
        name="Uniswap V3",
        protocol="uniswap_v3",
        chain_id=1,
        factory_address=V3_FACTORY,
        quoter_address=V3_QUOTER,
    )
    assert dex.factory_address == V3_FACTORY.lower()
    assert dex.quoter_address == V3_QUOTER.lower()


def test_dex_v2_has_no_quoter() -> None:
    dex = Dex(
        id=1,
        name="Uniswap V2",
        protocol="uniswap_v2",
        chain_id=1,
        factory_address=V2_FACTORY,
    )
    assert dex.quoter_address is None


def test_dex_rejects_unknown_protocol() -> None:
    with pytest.raises(ValidationError):
        Dex(
            id=1,
            name="Sushiswap",
            protocol="sushiswap",
            chain_id=1,
            factory_address=V2_FACTORY,
        )


# Pool


def test_pool_references_dex_by_id_and_fee_is_optional(weth: Token, usdc: Token) -> None:
    pool = Pool(address=POOL, dex_id=2, tokens=(weth, usdc))
    assert pool.dex_id == 2
    assert pool.fee is None


def test_pool_requires_positive_dex_id(weth: Token, usdc: Token) -> None:
    with pytest.raises(ValidationError):
        Pool(address=POOL, dex_id=0, tokens=(weth, usdc))


def test_pool_supports_more_than_two_tokens(weth: Token, usdc: Token, dai: Token) -> None:
    pool = Pool(address=POOL, dex_id=3, tokens=(weth, usdc, dai))
    assert len(pool.tokens) == 3


def test_pool_requires_at_least_two_tokens(weth: Token) -> None:
    with pytest.raises(ValidationError):
        Pool(address=POOL, dex_id=1, tokens=(weth))


def test_pool_rejects_duplicate_tokens(weth: Token, usdc: Token) -> None:
    with pytest.raises(ValidationError):
        Pool(address=POOL, dex_id=1, tokens=(weth, weth))

    with pytest.raises(ValidationError):
        Pool(address=POOL, dex_id=1, tokens=(weth, usdc, weth))


def test_pool_supports_pair(weth: Token, usdc: Token, dai: Token) -> None:
    pool = Pool(address=POOL, dex_id=1, tokens=(weth, usdc))
    assert pool.suppports_pair(weth, usdc)
    assert pool.suppports_pair(usdc, weth)
    assert not pool.suppports_pair(weth, dai)
    assert not pool.suppports_pair(weth, weth)


# Quote


def test_quote_requires_positive_amount_in(weth: Token, usdc: Token) -> None:
    pool = Pool(address=POOL, dex_id=1, tokens=(weth, usdc), fee=500)
    with pytest.raises(ValidationError):
        Quote(
            pool=pool,
            token_in=weth,
            token_out=usdc,
            amount_in=0,
            amount_out=0,
            pool_liquidity_out=0,
        )


def _quote(weth: Token, usdc: Token, **overrides: int) -> Quote:
    pool = Pool(address=POOL, dex_id=1, tokens=(weth, usdc), fee=500)
    values = {
        "amount_in": 10**18,
        "amount_out": 3_000 * 10**6,
        "pool_liquidity_out": 10**12,
    }
    return Quote(pool=pool, token_in=weth, token_out=usdc, **(values | overrides))


def test_quote_accepts_amounts_beyond_int64(weth: Token, usdc: Token) -> None:
    quote = _quote(weth, usdc, amount_in=10**19)
    assert quote.amount_in == 10**19


def test_quote_rejects_amount_above_uint256(weth: Token, usdc: Token) -> None:
    with pytest.raises(ValidationError):
        _quote(weth, usdc, amount_out=2**256)


def test_quote_rejects_negative_amount(weth: Token, usdc: Token) -> None:
    with pytest.raises(ValidationError):
        _quote(weth, usdc, pool_liquidity_out=-1)


def test_quote_amounts_are_strings_in_json_but_ints_in_python(weth: Token, usdc: Token) -> None:
    quote = _quote(weth, usdc, amount_in=10**18)

    assert quote.model_dump()["amount_in"] == 10**18
    assert json.loads(quote.model_dump_json())["amount_in"] == "1000000000000000000"
