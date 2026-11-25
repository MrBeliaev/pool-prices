from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)

# EVM address 0x + 40 hex-symbols
ADDRESS_PATTERN = r"^0x[0-9a-fA-F]{40}$"
Address = Annotated[str, Field(pattern=ADDRESS_PATTERN), AfterValidator(str.lower)]

UINT256_MAX = 2**256 - 1
Uint256 = Annotated[
    int,
    Field(ge=0, le=UINT256_MAX),
    PlainSerializer(str, return_type=str, when_used="json"),
]

DexProtocol = Literal["uniswap_v2", "uniswap_v3", "uniswap_v4"]


class Token(BaseModel):
    model_config = ConfigDict(frozen=True)

    chain_id: int = Field(gt=0)
    address: Address
    symbol: str = Field(min_length=1)
    decimals: int = Field(ge=0, le=36)


class Dex(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    chain_id: int = Field(gt=0)
    name: str = Field(min_length=1)
    protocol: DexProtocol
    factory_address: Address
    quoter_address: Address | None = None
    fee: int | None = Field(default=None, ge=0, lt=1_000_000)

    @model_validator(mode="after")
    def fee_matches_protocol(self) -> Self:
        is_v2 = self.protocol == "uniswap_v2"
        if is_v2 and self.fee is None:
            raise ValueError("uniswap v2 dex requires a default fee")
        if not is_v2 and self.fee is not None:
            raise ValueError("dex-level fee is only supported for uniswap v2")

        return self


class Pool(BaseModel):
    model_config = ConfigDict(frozen=True)

    dex_id: int = Field(gt=0)
    address: Address
    # Curve, Balancer > 2 tokens
    tokens: tuple[Token, ...] = Field(min_length=2)
    # 0.3% = 3000
    fee: int = Field(ge=0, lt=1000000)

    @model_validator(mode="after")
    def tokens_must_be_unique(self) -> Self:
        if len(set(self.tokens)) != len(self.tokens):
            raise ValueError("pool tokens must be unique")
        return self

    def suppports_pair(self, token_in: Token, token_out: Token) -> bool:
        return token_in != token_out and token_in in self.tokens and token_out in self.tokens


class Quote(BaseModel):
    model_config = ConfigDict(frozen=True)

    pool: Pool
    token_in: Token
    token_out: Token
    amount_in: Annotated[Uint256, Field(gt=0)]
    amount_out: Uint256
    pool_liquidity_out: Uint256
