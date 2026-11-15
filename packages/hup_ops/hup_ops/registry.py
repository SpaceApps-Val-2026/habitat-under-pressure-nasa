"""Shared operation registry."""
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Context:
    zone: dict
    period: dict
    seed: int
    data: object
    progress: Callable[[float, str], None]


@dataclass(frozen=True)
class Operation:
    id: str
    version: str
    fn: Callable[[dict, dict[str, list[dict]], Context], dict[str, dict]]


REGISTRY: dict[str, Operation] = {}


def operation(id: str, *, version: str):
    def register(fn):
        if id in REGISTRY:
            raise ValueError(f"Operation already registered: {id}")
        REGISTRY[id] = Operation(id, version, fn)
        return fn

    return register
