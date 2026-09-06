"""Versioned, non-executable data at controller boundaries."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$")]


class ContractError(ValueError):
    pass


def _normalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="json"))
    if value is None or type(value) in (bool, str, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ContractError("Non-finite numbers have no canonical identity")
        return 0.0 if value == 0 else value
    if type(value) in (list, tuple):
        return [_normalize(item) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: _normalize(item) for key, item in value.items()}
    raise ContractError("Only JSON values have a canonical identity")


def canonical(value: Any) -> bytes:
    return json.dumps(
        _normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def parse_json(data: bytes | str, *, max_bytes: int = 1024 * 1024) -> Any:
    raw = data.encode() if isinstance(data, str) else data
    if len(raw) > max_bytes:
        raise ContractError("JSON size limit exceeded")

    def pairs(items: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                raise ContractError("Duplicate JSON field")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ContractError(f"Invalid JSON numeric constant: {value}")

    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
        _normalize(result)
        return result
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ContractError("Invalid bounded JSON") from exc


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class Brief(StrictModel):
    original: str = Field(min_length=1, max_length=100_000)
    interpretation: str = Field(min_length=1)
    assumptions: list[str]
    exclusions: list[str]


class Budget(StrictModel):
    attempts: int = Field(ge=1, le=20)
    runtime_seconds: int = Field(ge=1, le=1200)
    provider_calls: int = Field(ge=0, le=20)
    tokens: int = Field(ge=0, le=1_000_000)


class Recipe(StrictModel):
    protocol_version: Literal["0.1"] = "0.1"
    source: Digest
    config: dict[str, JsonValue]
    environment: Digest
    inputs: list[Digest]
    randomization_unit: str
    seed: int
    evaluator: Digest
    analysis: Digest
    operation: str
    argv: list[str]
    runtime: dict[str, JsonValue]
    presentation: dict[str, str] = Field(default_factory=dict)

    @property
    def identity(self) -> str:
        return digest(self.model_dump(mode="json", exclude={"presentation"}))


class CampaignContract(StrictModel):
    contract_version: Literal["0.1"] = "0.1"
    id: Identifier
    project_id: Identifier
    brief: Brief
    kind: Literal["empirical_comparison", "simulation", "numerical_check", "counterexample"]
    target_claim: str
    baseline: Digest
    candidates: list[Digest] = Field(min_length=1, max_length=20)
    recipes: list[Recipe] = Field(min_length=2, max_length=20)
    allowed_interventions: list[str]
    controls: dict[str, JsonValue]
    data: Digest
    evaluator: Digest
    analysis: Digest
    metric: str
    practical_threshold: float = Field(ge=0)
    selection_rule: str
    stopping_rule: Literal["evaluate_each_once"]
    uncertainty_unit: str
    accepted_findings: list[Literal["supported_in_scope", "not_supported", "inconclusive"]]
    profile: Literal["development", "isolated-local"]
    capabilities: list[str]
    data_classification: Literal["public_synthetic"]
    budget: Budget

    @model_validator(mode="after")
    def identities_resolve(self) -> CampaignContract:
        identities = [recipe.identity for recipe in self.recipes]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate recipes are not independent replication")
        if set(identities) != {self.baseline, *self.candidates}:
            raise ValueError("Baseline and candidates must bind exactly to declared recipes")
        if any(r.evaluator != self.evaluator or r.analysis != self.analysis for r in self.recipes):
            raise ValueError("Recipe instruments differ from the contract")
        if self.budget.attempts < len(self.recipes):
            raise ValueError("Insufficient attempt budget for the predeclared plan")
        return self


class TaskSpec(StrictModel):
    id: Identifier
    campaign_id: Identifier
    recipe: Digest
    operation: Literal["execute"] = "execute"
    dependencies: list[Identifier] = Field(default_factory=list)
    max_attempts: int = Field(default=1, ge=1, le=20)
    profile: Literal["development", "isolated-local"]
    required_capabilities: list[str] = Field(default_factory=list)
    budget: dict[str, int]
    resources: dict[str, int]


class Request(StrictModel):
    protocol_version: Literal["0.1"] = "0.1"
    task_id: Identifier
    attempt_id: Identifier
    contract_digest: Digest
    operation: Literal["execute", "predict", "check_proof"]
    input_artifacts: list[Digest]
    output_directory: Literal["output"] = "output"
    operation_parameters: dict[str, JsonValue]


class ProducedArtifact(StrictModel):
    path: str = Field(min_length=1, max_length=500)
    media_type: Literal["application/json", "text/plain"]
    complete: Literal[True]


class Response(StrictModel):
    protocol_version: Literal["0.1"]
    attempt_id: Identifier
    status: Literal["success", "failure"]
    produced_artifacts: list[ProducedArtifact] = Field(max_length=32)
    diagnostics: dict[str, JsonValue]


def validate_response(data: bytes, attempt_id: str, returncode: int | None) -> Response:
    response = Response.model_validate(parse_json(data, max_bytes=64 * 1024))
    if returncode != 0 or response.status != "success":
        raise ContractError("Execution did not succeed")
    if response.attempt_id != attempt_id:
        raise ContractError("Response attempt identity mismatch")
    paths = [artifact.path for artifact in response.produced_artifacts]
    if not paths or len(paths) != len(set(paths)):
        raise ContractError("Missing or duplicate output artifacts")
    return response
