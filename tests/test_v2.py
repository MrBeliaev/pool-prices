import pytest
from web3 import AsyncWeb3

from pool_prices.dex import v2
from pool_prices.dex.v2 import fetch_reserves, get_amount_out, quote, quote_from_reserves
from pool_prices.errors import InsufficientLiquidityError, UnsupportedPairError
from pool_prices.models import Pool, Token

WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
PAIR = "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"

ONE_WETH = 10**18


@pytest.fixture
def weth() -> Token:
    return Token(chain_id=1, address=WETH, symbol="WETH", decimals=18)


@pytest.fixture
def usdc() -> Token:
    return Token(chain_id=1, address=USDC, symbol="USDC", decimals=6)


@pytest.fixture
def dai() -> Token:
    return Token(chain_id=1, address=DAI, symbol="DAI", decimals=18)


@pytest.fixture
def pool(weth: Token, usdc: Token) -> Pool:
    return Pool(address=PAIR, dex_id=1, tokens=(weth, usdc), fee=3000)


USDC_RESERVE = 2_000_000 * 10**6
WETH_RESERVE = 1_000 * 10**18
RESERVES = (USDC_RESERVE, WETH_RESERVE)


def test_get_amount_out_known_value() -> None:
    assert get_amount_out(100, 1000, 1000, 3000) == 90


@pytest.mark.parametrize("amount_in", [1, 7, 1_000, 10**6, 10**18, 10**21])
def test_get_amount_out_never_drains_pool_and_keeps_invariant(amount_in: int) -> None:
    reserve_in, reserve_out = 5 * 10**21, 7 * 10**12
    amount_out = get_amount_out(amount_in, reserve_in, reserve_out, 3000)

    assert 0 <= amount_out < reserve_out
    assert (reserve_in + amount_in) * (reserve_out - amount_out) >= reserve_in * reserve_out


def test_get_amount_out_rejects_zero_amount() -> None:
    with pytest.raises(ValueError):
        get_amount_out(0, 1000, 1000, 3000)


@pytest.mark.parametrize("reserves", [(0, 1000), (1000, 0), (0, 0)])
def test_get_amount_out_rejects_empty_reserves(reserves: tuple[int, int]) -> None:
    with pytest.raises(InsufficientLiquidityError):
        get_amount_out(100, *reserves, 3000)


def test_quote_weth_to_usdc_uses_reserves_in_contract_order(
    pool: Pool, weth: Token, usdc: Token
) -> None:
    quote = quote_from_reserves(pool, weth, usdc, ONE_WETH, RESERVES)

    assert quote.amount_out == get_amount_out(ONE_WETH, WETH_RESERVE, USDC_RESERVE, 3000)
    assert quote.pool_liquidity_out == USDC_RESERVE
    assert 1_990 * 10**6 < quote.amount_out < 2_000 * 10**6


def test_quote_usdc_to_weth_is_the_reverse_direction(pool: Pool, weth: Token, usdc: Token) -> None:
    quote = quote_from_reserves(pool, usdc, weth, 2_000 * 10**6, RESERVES)

    assert quote.amount_out == get_amount_out(2_000 * 10**6, USDC_RESERVE, WETH_RESERVE, pool.fee)
    assert quote.pool_liquidity_out == WETH_RESERVE


def test_quote_rejects_token_not_in_pool(pool: Pool, weth: Token, dai: Token) -> None:
    with pytest.raises(UnsupportedPairError):
        quote_from_reserves(pool, weth, dai, ONE_WETH, RESERVES)


def test_quote_rejects_same_token(pool: Pool, weth: Token) -> None:
    with pytest.raises(UnsupportedPairError):
        quote_from_reserves(pool, weth, weth, ONE_WETH, RESERVES)


def test_quote_rejects_pool_with_three_tokens(weth: Token, usdc: Token, dai: Token) -> None:
    three = Pool(address=PAIR, dex_id=1, tokens=(weth, usdc, dai), fee=3000)
    with pytest.raises(UnsupportedPairError):
        quote_from_reserves(three, weth, usdc, ONE_WETH, RESERVES)


class _FakeCall:
    def __init__(self, result: tuple[int, int, int]) -> None:
        self._result = result

    async def call(self) -> tuple[int, int, int]:
        return self._result


class _FakeFunctions:
    def __init__(self, result: tuple[int, int, int]) -> None:
        self._result = result

    def getReserves(self) -> _FakeCall:  # noqa: N802 (имя функции контракта)
        return _FakeCall(self._result)


class _FakeContract:
    def __init__(self, result: tuple[int, int, int]) -> None:
        self.functions = _FakeFunctions(result)


class _FakeEth:
    def __init__(self, result: tuple[int, int, int]) -> None:
        self._result = result
        self.contract_addresses: list[str] = []

    def contract(self, address: str, abi: list[dict[str, object]]) -> _FakeContract:
        self.contract_addresses.append(address)
        return _FakeContract(self._result)


class _FakeW3:
    def __init__(self, result: tuple[int, int, int]) -> None:
        self.eth = _FakeEth(result)


async def test_fetch_reserves_uses_checksum_address_and_drops_timestamp() -> None:
    w3 = _FakeW3((USDC_RESERVE, WETH_RESERVE, 1_700_000_000))

    reserves = await fetch_reserves(w3, PAIR.lower())  # type: ignore[arg-type]

    assert reserves == (USDC_RESERVE, WETH_RESERVE)
    assert w3.eth.contract_addresses == [AsyncWeb3.to_checksum_address(PAIR)]


async def test_quote_reads_reserves_then_calculates(
    pool: Pool, weth: Token, usdc: Token, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_fetch(w3: object, pair_address: str) -> tuple[int, int]:
        assert pair_address == pool.address
        return RESERVES

    monkeypatch.setattr(v2, "fetch_reserves", fake_fetch)

    _quote = await quote(None, pool, weth, usdc, ONE_WETH)

    assert _quote.amount_out == get_amount_out(ONE_WETH, WETH_RESERVE, USDC_RESERVE, pool.fee)
