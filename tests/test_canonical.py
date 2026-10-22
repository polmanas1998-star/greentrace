import math

import pytest

from app.canonical import canonical_json, sha256_of


def test_key_order_does_not_change_text():
    assert canonical_json({"b": 1, "a": [2, {"d": 3, "c": 4}]}) == canonical_json(
        {"a": [2, {"c": 4, "d": 3}], "b": 1}
    )


def test_floats_rounded_to_six_decimals():
    assert canonical_json({"x": 0.1234567}) == '{"x":0.123457}'


def test_negative_zero_hashes_like_zero():
    assert sha256_of({"x": -0.0000001}) == sha256_of({"x": 0.0})


def test_non_finite_number_refused():
    with pytest.raises(ValueError):
        canonical_json({"x": math.nan})


def test_unknown_type_refused():
    with pytest.raises(TypeError):
        canonical_json({"x": {1, 2}})
