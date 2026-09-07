import copy
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from kestrel.contracts import Recipe, canonical, digest, parse_json, validate_response


def recipe():
    return Recipe(source="a" * 64, config={"step": 0.5}, environment="b" * 64,
                  inputs=["c" * 64], randomization_unit="fixed-domain", seed=7,
                  evaluator="d" * 64, analysis="e" * 64, operation="predict",
                  argv=["python", "worker.py"], runtime={"python": "3.13"})


@pytest.mark.acceptance("A09")
@given(st.dictionaries(st.text(max_size=20), st.floats(allow_nan=False, allow_infinity=False),
                       max_size=20))
def test_canonical_key_order_and_floats(value):
    assert digest(value) == digest(dict(reversed(list(value.items()))))
    assert parse_json(canonical(value)) == value


@pytest.mark.acceptance("A09")
def test_all_behavior_fields_change_identity():
    original = recipe()
    changes = {"source": "f" * 64, "config": {"step": 0.6}, "environment": "f" * 64,
               "inputs": ["f" * 64], "randomization_unit": "other", "seed": 8,
               "evaluator": "f" * 64, "analysis": "f" * 64, "operation": "execute",
               "argv": ["python", "new.py"], "runtime": {"python": "3.12"}}
    for field, value in changes.items():
        updated = Recipe.model_validate({**original.model_dump(), field: value})
        assert updated.identity != original.identity, field
    assert Recipe.model_validate({**original.model_dump(), "presentation": {"title": "new"}}).identity == original.identity
    assert digest(-0.0) == digest(0.0)
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            digest(value)


def test_json_strictness():
    for value in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e9999}', b'\xff'):
        with pytest.raises(ValueError):
            parse_json(value)
    with pytest.raises(ValueError):
        parse_json(b'"oversized"', max_bytes=3)
    with pytest.raises(ValidationError):
        Recipe.model_validate({**recipe().model_dump(), "seed": "7"})


def response():
    return {"protocol_version": "0.1", "attempt_id": "attempt-1", "status": "success",
            "produced_artifacts": [{"path": "result.json", "media_type": "application/json",
                                    "complete": True}], "diagnostics": {}}


@pytest.mark.acceptance("A10")
@pytest.mark.parametrize("change", [{"protocol_version": "1.0"}, {"attempt_id": "wrong"},
                                     {"produced_artifacts": []}, {"trusted": True},
                                     {"status": "failure"}])
def test_malformed_adapter_response(change):
    value = copy.deepcopy(response())
    value.update(change)
    with pytest.raises(ValueError):
        validate_response(canonical(value), "attempt-1", 0)


@pytest.mark.acceptance("A10")
def test_oversized_and_failed_response():
    with pytest.raises(ValueError):
        validate_response(b" " * 65537, "attempt-1", 0)
    with pytest.raises(ValueError):
        validate_response(canonical(response()), "attempt-1", 1)
