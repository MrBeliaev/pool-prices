import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Protocol

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pool_prices.chain.node_client import open_web3
from pool_prices.config import get_settings
from pool_prices.db import open_db
from pool_prices.errors import NoRouteError, QuotersFailedError, UnsupportedPairError
from pool_prices.models import ADDRESS_PATTERN, UINT256_MAX, Quote
from pool_prices.service import PriceResult, PriceService

logger = logging.getLogger(__name__)


class QuoteProvider(Protocol):
    async def best_quote(
        self, chain_id: int, token_in: str, token_out: str, amount_in: int
    ) -> PriceResult: ...


class QuoteResponse(BaseModel):
    best: Quote
    alternatives: list[Quote]


class ErrorResponse(BaseModel):
    error: str
    detail: str


def get_provider(request: Request) -> QuoteProvider:
    provider: QuoteProvider = request.app.state.provider
    return provider


def create_app(provider: QuoteProvider | None = None) -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if provider is not None:
            yield
            return
        settings = get_settings()
        async with (
            open_db(settings.database_url) as db,
            open_web3(settings.rpc_url, timeout_seconds=settings.request_timeout_seconds) as w3,
        ):
            app.state.provider = PriceService(
                db, w3, total_timeout=settings.request_timeout_seconds
            )
            yield

    app = FastAPI(title="pool-prices", version="0.1.0", lifespan=lifespan)
    if provider is not None:
        app.state.provider = provider

    def error(status: int, name: str, exc: Exception) -> JSONResponse:
        body = ErrorResponse(error=name, detail=str(exc))
        return JSONResponse(status_code=status, content=body.model_dump())

    @app.exception_handler(NoRouteError)
    async def no_route(_: Request, exc: NoRouteError) -> JSONResponse:
        return error(404, "no_route", exc)

    @app.exception_handler(UnsupportedPairError)
    async def unsupported(_: Request, exc: UnsupportedPairError) -> JSONResponse:
        return error(422, "unsupported_pair", exc)

    @app.exception_handler(QuotersFailedError)
    async def quoters_failed(_: Request, exc: QuotersFailedError) -> JSONResponse:
        return error(502, "upstream_failed", exc)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/quote", response_model=QuoteResponse)
    async def quote(
        provider: Annotated[QuoteProvider, Depends(get_provider)],
        chain_id: Annotated[int, Query(gt=0)],
        token_in: Annotated[str, Query(pattern=ADDRESS_PATTERN)],
        token_out: Annotated[str, Query(pattern=ADDRESS_PATTERN)],
        amount_in: Annotated[str, Query(pattern=r"^[0-9]{1,78}$")],
    ) -> QuoteResponse:
        amount = int(amount_in)
        if not 0 < amount <= UINT256_MAX:
            raise HTTPException(status_code=422, detail="amount_in mast be in (0, 2**256)")
        result = await provider.best_quote(chain_id, token_in, token_out, amount)

        return QuoteResponse(best=result.best, alternatives=list(result.alternatives))

    return app
