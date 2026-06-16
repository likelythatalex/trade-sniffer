"""Load, validate, and expose ``config.yaml`` as typed objects (SPEC §4.3).

Validation **fails fast** — before any network fetch — so a misconfigured run
dies with a clear message instead of producing a misleading report. This module
also resolves the per-timeframe Wyckoff params (``defaults`` merged with
``per_timeframe[tf]``) so strategy code never merges config itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")


@dataclass
class Config:
    """Typed view over ``config.yaml``.

    Stub: ``raw`` holds the parsed YAML for now. As each module lands, promote the
    sections it consumes (``data``, ``features``, ``wyckoff``, ``scoring``,
    ``output``, ``liquidity``, ``strategies``) into typed fields so downstream code
    never touches raw dicts.
    """

    raw: dict[str, Any]


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Parse and validate ``config.yaml``; raise on the first problem (fail fast)."""
    raise NotImplementedError


def resolve_wyckoff_params(config: Config, timeframe: str) -> dict[str, Any]:
    """Return Wyckoff params for ``timeframe``: ``defaults`` overlaid with
    ``per_timeframe[tf]`` (key by key). The single place config merging happens."""
    raise NotImplementedError


def _validate(raw: dict[str, Any]) -> None:
    """Fail fast (§4.3) when, e.g.:

    - required keys are missing;
    - ``weekly`` is in ``timeframes`` without ``wyckoff.per_timeframe.weekly``;
    - ``notify.channel`` is anything other than ``discord`` in v1;
    - a referenced env var name is set but empty;
    - ``wyckoff.sub_weights`` do not sum to 100;
    - a timeframe's lookback < scoring window + ``features.baseline_window`` (warmup).
    """
    raise NotImplementedError
