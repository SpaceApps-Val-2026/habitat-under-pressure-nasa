"""Cooperative cancellation for readers, including their own worker threads."""
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from itertools import islice
from threading import Event


class Signal(Event):
    requested_at: float | None = None

    def request(self) -> None:
        self.requested_at = time.perf_counter()
        self.set()


signal: ContextVar[Signal | None] = ContextVar("read_cancellation", default=None)


class Cancelled(BaseException):
    """Leave the reader without translating cancellation into a missing source or a partial result."""


def checkpoint() -> None:
    event = signal.get()
    if event is not None and event.is_set():
        raise Cancelled


def call[Result](fn: Callable[..., Result], *args) -> Result:
    checkpoint()
    return fn(*args)


def map_items[Item, Result](fn: Callable[[Item], Result], items: Iterable[Item], *,
                            workers: int) -> Iterator[Result]:
    """Read a bounded number of items ahead and return them in the original order."""
    checkpoint()
    iterator = iter(items)
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        pending = deque(pool.submit(copy_context().run, call, fn, item) for item in islice(iterator, workers))
        for item in iterator:
            checkpoint()
            result = pending.popleft().result()
            checkpoint()
            pending.append(pool.submit(copy_context().run, call, fn, item))
            yield result
        while pending:
            checkpoint()
            result = pending.popleft().result()
            checkpoint()
            yield result
        checkpoint()
    finally:
        pool.shutdown(cancel_futures=True)
