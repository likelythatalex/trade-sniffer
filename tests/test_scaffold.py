"""Scaffold smoke tests — confirm the package wiring is sound before logic lands.

These intentionally test *structure*, not behavior. Real behavior tests (SPEC §11:
wyckoff fixtures, features NaN handling, config validation, state transitions,
no-lookahead, golden output) get added alongside each module's implementation.
"""
from __future__ import annotations

import importlib

import pytest

MODULES = [
    "src.config",
    "src.universe",
    "src.data",
    "src.data_quality",
    "src.features",
    "src.combiner",
    "src.scanner",
    "src.state",
    "src.report",
    "src.notify",
    "src.strategies.base",
    "src.strategies.registry",
    "src.strategies.wyckoff",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name: str) -> None:
    """Every package module imports cleanly (catches syntax/import-wiring errors)."""
    importlib.import_module(module_name)


def test_registry_resolves_wyckoff() -> None:
    """The registry maps the config name 'wyckoff' to a Strategy instance."""
    from src.strategies.base import Strategy
    from src.strategies.registry import get_strategy

    strategy = get_strategy("wyckoff")
    assert isinstance(strategy, Strategy)
    assert strategy.name == "wyckoff"


def test_registry_unknown_strategy_fails_loud() -> None:
    """An unknown name raises (a config typo must not silently disable a strategy)."""
    from src.strategies.registry import get_strategy

    with pytest.raises(KeyError):
        get_strategy("does_not_exist")
