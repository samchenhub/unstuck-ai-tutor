import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay: float = 1.0,
    sleep: Callable[[float], None] | None = None,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    give_up_on: tuple[type[BaseException], ...] = (),
) -> T:
    """Call fn, retrying listed errors with exponential backoff (base, 2*base, 4*base…).
    Errors in give_up_on propagate immediately even when retry_on would match them."""
    sleep = sleep or time.sleep
    for attempt in range(attempts):
        try:
            return fn()
        except give_up_on:
            raise
        except retry_on:
            if attempt == attempts - 1:
                raise
            sleep(base_delay * 2**attempt)
    raise AssertionError("unreachable")
