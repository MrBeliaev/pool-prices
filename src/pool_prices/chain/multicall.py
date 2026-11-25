import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from web3 import AsyncWeb3

from pool_prices.retry import retry

MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"

DEFAULT_CHUNK_SIZE = 200
DEFAULT_MAX_CONCURRENT = 4

MULTICALL3_ABI: list[dict[str, Any]] = [
    {
        "name": "aggregate3",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {
                "name": "calls",
                "type": "tuple[]",
                "components": [
                    {"name": "target", "type": "address"},
                    {"name": "allowFailure", "type": "bool"},
                    {"name": "callData", "type": "bytes"},
                ],
            }
        ],
        "outputs": [
            {
                "name": "returnData",
                "type": "tuple[]",
                "components": [
                    {"name": "success", "type": "bool"},
                    {"name": "returnData", "type": "bytes"},
                ],
            }
        ],
    }
]


@dataclass(frozen=True, slots=True)
class Call:
    target: str
    call_data: bytes
    allow_failure: bool = True


@dataclass(frozen=True, slots=True)
class CallResult:
    success: bool
    return_data: bytes


def selector(signature: str) -> bytes:
    return bytes(AsyncWeb3.keccak(text=signature)[:4])


async def aggregate3(
    w3: AsyncWeb3[Any],
    calls: Sequence[Call],
    *,
    multicall_address: str = MULTICALL3_ADDRESS,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    retry_attempts: int = 3,
) -> list[CallResult]:
    if chunk_size <= 0 or max_concurrent <= 0:
        raise ValueError("chunk_size and max_concurrent must be positive")
    if not calls:
        return []

    contract = w3.eth.contract(
        address=AsyncWeb3.to_checksum_address(multicall_address), abi=MULTICALL3_ABI
    )
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_chunk(chunk: Sequence[Call]) -> list[CallResult]:
        payload = [
            (AsyncWeb3.to_checksum_address(call.target), call.allow_failure, call.call_data)
            for call in chunk
        ]

        async with semaphore:
            raw = await retry(
                lambda: contract.functions.aggregate3(payload).call(), attempts=retry_attempts
            )
        return [CallResult(bool(success), bytes(data)) for success, data in raw]

    chunks = [calls[start : start + chunk_size] for start in range(0, len(calls), chunk_size)]
    tasks = [asyncio.create_task(run_chunk(chunk)) for chunk in chunks]
    try:
        per_chunk = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        raise

    return [result for chunk_results in per_chunk for result in chunk_results]
