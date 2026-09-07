import hashlib

import pytest

from kestrel.contracts import canonical, digest
from kestrel.evaluation import (
    comparison,
    evaluate,
    instrument,
    validate_quantitative_claim,
)


def observe(payload, kind="numerical", method="baseline", evaluator=None):
    registered, data = instrument(kind)
    return evaluate(kind=kind, payload=canonical(payload), attempt_id="a1", recipe="a" * 64,
                    artifact=digest(payload), evaluator=evaluator or registered, data=data,
                    expected_method=method)


@pytest.mark.acceptance("A21")
def test_modified_evaluator_identity_cannot_certify():
    registered, _ = instrument("numerical")
    registered["code"] = "f" * 64
    with pytest.raises(ValueError, match="identity"):
        observe({}, evaluator=registered)


@pytest.mark.acceptance("A22")
@pytest.mark.acceptance("A23")
@pytest.mark.acceptance("A27")
def test_inferior_predictions_cannot_be_rescored_by_worker():
    baseline = observe({"kind": "predictions", "ids": list(range(5)),
                        "values": [-3, -1, 1, 3, 5], "self_reported_metric": 0})
    inferior = observe({"kind": "predictions", "ids": list(range(5)),
                        "values": [-1, 1, 3, 5, 7], "self_reported_metric": -999})
    assert inferior.metric == 4.0 and inferior.self_reported_metric_rejected
    result = comparison(baseline, inferior, 0.0)
    assert result["finding"] == "not_supported"
    assert result["execution"] == "succeeded" and result["validity"] == "valid"
    assert result["assurance"] == "independently_recomputed"


@pytest.mark.acceptance("A24")
@pytest.mark.parametrize("witnesses", [[], [0], [1], [0, 1, 2], [0, 0, 1]])
def test_analytic_oracle_defeats_wrong_counterexample_search(witnesses):
    with pytest.raises(ValueError, match="analytic"):
        observe({"kind": "counterexamples", "strict": True, "witnesses": witnesses,
                 "self_reported_metric": 0}, kind="counterexample", method="inferior")


@pytest.mark.acceptance("A24")
def test_known_counterexamples_verified():
    result = observe({"kind": "counterexamples", "strict": True, "witnesses": [0, 1],
                      "self_reported_metric": 0}, kind="counterexample", method="inferior")
    assert result.metric == 2


@pytest.mark.acceptance("A40")
def test_invented_report_number_rejected():
    analysis = canonical({"baseline": 0.0, "treatment": 4.0, "difference": 4.0})
    claim = {"analysis": hashlib.sha256(analysis).hexdigest(), "field": "difference", "value": -4}
    with pytest.raises(ValueError, match="not supported"):
        validate_quantitative_claim(claim, analysis)
    assert validate_quantitative_claim({**claim, "value": 4}, analysis)["value"] == 4
