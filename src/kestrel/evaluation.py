"""Protected finite-domain evaluators. No submitted code is executed here."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Literal

from pydantic import Field

from kestrel.contracts import Digest, StrictModel, canonical, digest, parse_json


class Predictions(StrictModel):
    kind: Literal["predictions"]
    ids: list[int] = Field(max_length=1000)
    values: list[float | int] = Field(max_length=1000)
    self_reported_metric: float | int


class Counterexamples(StrictModel):
    kind: Literal["counterexamples"]
    strict: bool
    witnesses: list[int] = Field(max_length=1000)
    self_reported_metric: float | int


class Observation(StrictModel):
    version: Literal["0.1"] = "0.1"
    attempt_id: str
    recipe: Digest
    evaluator: Digest
    data: Digest
    artifact: Digest
    metric_name: str
    metric: float
    self_reported_metric_rejected: bool
    execution: Literal["succeeded"] = "succeeded"
    validity: Literal["valid"] = "valid"
    assurance: Literal["independently_recomputed"] = "independently_recomputed"
    scope: str


ANALYSIS = {"version": "0.1", "method": "finite-domain exact comparison",
            "selection": "predeclared baseline and inferior candidate, each once",
            "uncertainty": "exhaustive fixture domain; no population inference"}


def instrument(kind: str) -> tuple[dict, dict]:
    code_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if kind == "numerical":
        data = {"xs": [-2, -1, 0, 1, 2], "targets": [-3, -1, 1, 3, 5]}
        method = "mean_squared_error"
    elif kind == "counterexample":
        data = {"domain": list(range(-10, 11)), "strict_counterexamples": [0, 1]}
        method = "counterexample_count"
    else:
        raise ValueError("No independently registered evaluator for this project kind")
    evaluator = {"version": "0.1", "kind": kind, "method": method, "code": code_hash}
    return evaluator, data


def evaluate(*, kind: str, payload: bytes, attempt_id: str, recipe: str, artifact: str,
             evaluator: dict, data: dict, expected_method: str) -> Observation:
    expected_evaluator, expected_data = instrument(kind)
    if evaluator != expected_evaluator or data != expected_data:
        raise ValueError("Registered evaluator/data identity has changed")
    value = parse_json(payload)
    if kind == "numerical":
        predictions = Predictions.model_validate(value)
        if predictions.ids != list(range(len(data["targets"]))) or len(predictions.values) != 5:
            raise ValueError("Predictions must exactly match protected target IDs and shape")
        if any(abs(x) > 1e6 for x in predictions.values):
            raise ValueError("Prediction exceeds finite fixture bounds")
        # Independent targets, no candidate module, local evaluator file or metric consumed.
        metric = math.fsum((float(x) - y) ** 2 for x, y in
                           zip(predictions.values, data["targets"], strict=True)) / 5
        reported = predictions.self_reported_metric
    else:
        counterexamples = Counterexamples.model_validate(value)
        if counterexamples.strict != (expected_method == "inferior"):
            raise ValueError("Candidate changed the frozen proposition")
        expected = data["strict_counterexamples"] if counterexamples.strict else []
        if counterexamples.witnesses != expected:
            raise ValueError("Known analytic counterexamples disagree with submitted output")
        metric = float(len(expected))
        reported = counterexamples.self_reported_metric
    return Observation(attempt_id=attempt_id, recipe=recipe, evaluator=digest(evaluator),
                       data=digest(data), artifact=artifact, metric_name=evaluator["method"],
                       metric=metric, self_reported_metric_rejected=(reported != metric),
                       scope="Only the frozen finite public synthetic fixture domain")


def comparison(baseline: Observation, treatment: Observation, threshold: float) -> dict:
    if baseline.evaluator != treatment.evaluator or baseline.data != treatment.data:
        raise ValueError("Instruments and input data must match")
    difference = treatment.metric - baseline.metric
    return {"baseline": baseline.metric, "treatment": treatment.metric, "difference": difference,
            "finding": "supported_in_scope" if difference < -threshold else "not_supported",
            "execution": "succeeded", "validity": "valid",
            "assurance": "independently_recomputed", "scope": treatment.scope,
            "uncertainty": ANALYSIS["uncertainty"], "analysis": digest(ANALYSIS)}


def validate_quantitative_claim(claim: dict, analysis_payload: bytes) -> dict:
    """Check a field reference only. Bytes and hashes alone confer no assurance."""
    if set(claim) != {"analysis", "field", "value"}:
        raise ValueError("A quantitative claim requires a computed field reference")
    if claim["analysis"] != hashlib.sha256(analysis_payload).hexdigest():
        raise ValueError("Analysis identity mismatch")
    analysis = parse_json(analysis_payload)
    if claim["field"] not in {"baseline", "treatment", "difference"}:
        raise ValueError("Unknown certified quantitative field")
    if type(claim["value"]) not in (float, int) or claim["value"] != analysis[claim["field"]]:
        raise ValueError("Claim number is not supported by its cited computed field")
    return {**claim, "validation": "field_reference_matches"}


def evaluator_bytes(kind: str) -> tuple[bytes, bytes]:
    evaluator, data = instrument(kind)
    return canonical(evaluator), canonical(data)
