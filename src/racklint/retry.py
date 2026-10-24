# racklint/src/racklint/retry.py
"""How often the readers try a device that does not answer, and how long they wait between tries."""

import asyncio

ATTEMPTS = 3
BACKOFF_SECONDS = 0.5


async def pause(attempt: int, backoff: float = BACKOFF_SECONDS) -> None:
    """Wait after failed try number `attempt` (from 0), twice as long as after the one before."""
    await asyncio.sleep(backoff * 2**attempt)
