"""One way to serialise JSON, used everywhere a result is hashed or compared.

Same data in, same text out: keys sorted, floats rounded to 6 decimals, no
whitespace. Two runs of the same case must give the same verdict hash, so
nothing time-dependent may ever be passed in here as part of a verdict.
"""
import hashlib
import json
import math
from typing import Any

DECIMALS = 6


def _normalise(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite number cannot be serialised: {value!r}")
        rounded = round(value, DECIMALS)
        return 0.0 if rounded == 0 else rounded  # -0.0 and 0.0 hash the same
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise TypeError(f"JSON keys must be strings, got {type(key).__name__}")
        return {key: _normalise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    raise TypeError(f"cannot serialise {type(value).__name__} to canonical JSON")


def canonical_json(value: Any) -> str:
    return json.dumps(
        _normalise(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
