"""Offline research loop: propose, authorize, execute, ingest, evaluate, report."""

from __future__ import annotations

import hashlib
import os
import platform
import secrets
import sys
import tempfile
import time
import uuid
from pathlib import Path

from kestrel.agent_execution import AgentPlan, OfflineAgents
from kestrel.agents import Proposal
from kestrel.artifacts import ArtifactError, Artifacts, _safe_read
from kestrel.contracts import (
    Brief,
    Budget,
    CampaignContract,
    Recipe,
    Request,
    TaskSpec,
    canonical,
    digest,
    outcome_matches,
    parse_json,
    validate_response,
)
from kestrel.controller import TERMINAL, Controller
from kestrel.evaluation import (
    ANALYSIS,
    Observation,
    comparison,
    evaluate,
    instrument,
    validate_quantitative_claim,
)
from kestrel.fixtures import WORKERS, generate, is_trusted_workspace
from kestrel.projects import Projects
from kestrel.runners import DevelopmentDriver, DriverError, JobSpec


def framework_root() -> Path:
    package = Path(__file__).resolve().parent
    return package.parent.parent if package.parent.name == "src" else package


def source_payload(workspace: Path) -> bytes:
    if not is_trusted_workspace(workspace):
        raise ValueError("First-pilot execution requires exact trusted fixture sources")
    return canonical({"version": "0.1", "files": {
        name: (workspace / name).read_text() for name in ("config.json", "worker.py")
    }})


class Lab:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        if self.root.is_relative_to(framework_root()) or framework_root().is_relative_to(self.root):
            raise ValueError("Lab/runtime must be external to framework source")
        if not (self.root / "lab.json").is_file():
            raise ValueError("Initialize this external developer lab first")
        for relative in ("lab.json", "definition", "runtime", "definition/projects.sqlite",
                         "runtime/controller.sqlite", "runtime/controller.sqlite-wal",
                         "runtime/controller.sqlite-shm", "runtime/jobs", "runtime/artifacts", "runtime/agents"):
            target = self.root / relative
            if target.is_symlink() or target.resolve() != target:
                raise ValueError("Developer lab state paths must not redirect through symlinks")
        self.projects = Projects(framework_root(), self.root / "definition", self.root / "runtime")
        self.store = Artifacts(self.root / "runtime" / "artifacts")
        self.controller = Controller(self.root / "runtime" / "controller.sqlite", evidence_store=self.store)
        self.driver = DevelopmentDriver(self.root / "runtime" / "jobs")
        self.agents = OfflineAgents(self.controller, self.store, self.root / "runtime" / "agents")

    def close(self) -> None:
        self.controller.close()
        self.projects.close()
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @classmethod
    def initialize(cls, root: Path) -> Lab:
        root = Path(root).resolve()
        if root.is_relative_to(framework_root()) or framework_root().is_relative_to(root):
            raise ValueError("Lab/runtime must be outside framework source")
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
        (root / "lab.json").write_bytes(canonical({"version": "0.1", "profile": "development"}))
        lab = cls(root)
        token = secrets.token_hex(32)
        token_path = root / "operator.token"
        with token_path.open("x") as stream:
            os.chmod(token_path, 0o600)
            stream.write(token)
        lab.controller.initialize_operator(token)
        return lab

    def register(self, manifest: Path, *, snapshot_dirty: bool) -> dict:
        manifest = manifest.resolve(strict=True)
        if manifest.is_relative_to(framework_root()):
            raise ValueError("Sidecar must be supplied outside framework source")
        target = self.root / "definition" / f"sidecar-{uuid.uuid4().hex}.json"
        if manifest != target:
            # Copy bounded data; registration still executes no source programs.
            raw = _safe_read(manifest.parent, manifest.name, 1024 * 1024)
            target.write_bytes(raw)
        return self.projects.register(target, snapshot_dirty=snapshot_dirty)

    def _put(self, value, *, producer: str, lineage=(), assurance="traceable", historical=False) -> dict:
        return self.store.put_bytes(canonical(value), producer=producer,
                                    media_type="application/json", lineage=lineage,
                                    assurance=assurance, historical=historical)

    def propose(self, project_id: str, brief: str, *, kind: str | None = None) -> str:
        record = self.projects.get(project_id)
        snapshot = Path(record["snapshot_path"])
        if not is_trusted_workspace(snapshot):
            raise ValueError("External project registered; arbitrary code execution needs an isolated profile")
        if record["manifest"]["data_policy"] != {
            "classification": "public_synthetic", "external_model_access": False
        }:
            raise ValueError("Offline fixture campaigns require public synthetic data without model access")
        detected = next(k for k, code in WORKERS.items()
                        if (snapshot / "worker.py").read_text() == code)
        if kind is not None and kind != detected:
            raise ValueError("Declared project kind does not match the approved fixture instrument")
        kind = detected
        evaluator, data = instrument(kind)
        evaluator_record = self._put(evaluator, producer="controller:evaluator-registry")
        data_record = self._put(data, producer="controller:fixture-data")
        analysis_record = self._put(ANALYSIS, producer="controller:analysis-registry")
        environment = {"python": platform.python_version(), "executable_sha256":
                       hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                       "platform": platform.platform(), "profile": "development"}
        environment_record = self._put(environment, producer="controller:environment")
        # Preparing this finite fixture template is not an agent invocation.
        # Its actual bounded mock attempt runs only after explicit approval.
        planned = [Proposal(path="config.json", content='{"method":"inferior"}')]
        agent_plan = AgentPlan(expected_proposals=planned)
        plan_record = self._put(agent_plan, producer="controller:agent-plan")
        recipes = []
        for method in ("baseline", "inferior"):
            changes = {} if method == "baseline" else {
                p.path: p.content.encode() for p in planned
            }
            workspace = self.projects.candidate(project_id, f"proposal-{uuid.uuid4().hex}", changes)
            source = self.store.put_bytes(source_payload(workspace), producer="controller:source",
                                          media_type="application/json")
            recipe = Recipe(source=source["digest"], config={"method": method},
                            environment=environment_record["digest"], inputs=[data_record["digest"]],
                            randomization_unit="exhaustive-fixed-domain", seed=0,
                            evaluator=evaluator_record["digest"], analysis=analysis_record["digest"],
                            operation="predict" if kind == "numerical" else "check_proof",
                            argv=[sys.executable, "-I", "-B", "worker.py"], runtime=environment)
            recipes.append(recipe)
        contract = CampaignContract(
            id=f"fixture-{uuid.uuid4().hex}", project_id=project_id,
            brief=Brief(original=brief, interpretation="Compare the frozen fixture candidates honestly",
                        assumptions=["Finite deterministic synthetic domain; exact oracle is applicable"],
                        exclusions=["No claim about a population or real research project"]),
            kind="numerical_check" if kind == "numerical" else "counterexample",
            target_claim="The predeclared treatment improves the finite-domain metric",
            baseline=recipes[0].identity, candidates=[recipes[1].identity], recipes=recipes,
            allowed_interventions=["config.json method baseline to inferior"],
            controls={"domain": "fixed", "search": "none", "agent_plan": plan_record["digest"],
                      "provider_calls": 0, "kind": kind},
            data=data_record["digest"], evaluator=evaluator_record["digest"],
            analysis=analysis_record["digest"], metric=evaluator["method"], practical_threshold=0.0,
            selection_rule="Predeclared baseline and inferior candidate; retain all outcomes",
            stopping_rule="evaluate_each_once", uncertainty_unit="entire finite fixture domain",
            accepted_findings=["supported_in_scope", "not_supported", "inconclusive"],
            profile="development", capabilities=["execute", "offline_agent"], data_classification="public_synthetic",
            budget=Budget(attempts=3, runtime_seconds=11, provider_calls=0, tokens=0),
        )
        campaign_id = self.controller.propose(contract.model_dump(mode="json"))
        self._ensure_contract_artifact(campaign_id)
        agent_task = TaskSpec(id=f"agent-{campaign_id}", campaign_id=campaign_id,
                              recipe=plan_record["digest"], operation="offline_agent", profile="development",
                              budget={"runtime_seconds": 1, "provider_calls": 0, "tokens": 0},
                              resources={"cpu": 1})
        self.controller.add_task(campaign_id, agent_task.model_dump(mode="json"))
        for index, recipe in enumerate(recipes):
            task = TaskSpec(id=f"task-{campaign_id}-{index}", campaign_id=campaign_id,
                            recipe=recipe.identity, profile="development",
                            budget={"runtime_seconds": 5, "provider_calls": 0, "tokens": 0},
                            resources={"cpu": 1}, dependencies=[agent_task.id])
            self.controller.add_task(campaign_id, task.model_dump(mode="json"))
        self.controller.record_selection(campaign_id, candidate_id=recipes[1].identity,
                                         rationale=contract.selection_rule, observation_ids=[])
        self.controller.freeze(campaign_id)
        return campaign_id

    def _ensure_contract_artifact(self, campaign_id: str) -> None:
        """Register authoritative controller amendments at the application boundary.

        Existing records are never rewritten or given stronger trust labels.
        """
        campaign = self.controller.campaign(campaign_id)
        try:
            self.store.get(campaign["digest"])
        except ArtifactError:
            contract = CampaignContract.model_validate(campaign["contract"])
            parents = [contract.data, contract.evaluator, contract.analysis,
                       contract.controls["agent_plan"],
                       *[r.environment for r in contract.recipes],
                       *[r.source for r in contract.recipes]]
            self._put(contract, producer=f"contract:{campaign_id}", lineage=parents,
                      historical=True)

    def approve(self, campaign_id: str, contract_digest: str, token: str) -> str:
        capabilities = self.controller.campaign(campaign_id)["contract"]["capabilities"]
        return self.controller.approve(campaign_id, token=token, contract_digest=contract_digest,
                                       principal="developer", expires_at=time.time() + 3600,
                                       capabilities=[name for name in ("execute", "offline_agent") if name in capabilities])

    def _inspection(self, attempt: dict) -> dict:
        status = self.driver.reconcile(attempt["backend_label"])
        return {"backend_label": attempt["backend_label"], "status": status.state,
                "never_launched": status.state == "absent"}

    def _prepare(self, contract: CampaignContract, attempt: dict) -> tuple[Recipe, Path, JobSpec]:
        task = self.controller.task(attempt["task_id"])["spec"]
        recipe = next(r for r in contract.recipes if r.identity == task["recipe"])
        for identity in (digest(contract), recipe.source, recipe.environment, contract.evaluator,
                         contract.analysis, contract.data):
            if self.store.get(identity)["status"] != "valid":
                raise ValueError("Frozen input or instrument has been invalidated")
        environment = parse_json(self.store.read(recipe.environment))
        if environment["python"] != platform.python_version() or environment["executable_sha256"] != hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest():
            raise ValueError("Frozen execution environment differs from the installed interpreter")
        source = parse_json(self.store.read(recipe.source))
        name = attempt["backend_label"]
        workspace = self.projects.candidates / contract.project_id / name
        if not workspace.exists():
            workspace = self.projects.candidate(contract.project_id, name,
                                                 {n: c.encode() for n, c in source["files"].items()})
        if hashlib.sha256(source_payload(workspace)).hexdigest() != recipe.source:
            raise ValueError("Candidate source no longer matches frozen recipe")
        data = parse_json(self.store.read(contract.data))
        kind = contract.controls["kind"]
        parameters = {"xs": data["xs"]} if kind == "numerical" else {"domain": data["domain"]}
        request = Request(task_id=attempt["task_id"], attempt_id=attempt["id"],
                          contract_digest=digest(contract), operation=recipe.operation,
                          input_artifacts=recipe.inputs, operation_parameters=parameters)
        return recipe, workspace, JobSpec(attempt_id=attempt["backend_label"], workspace=workspace,
                                          argv=tuple(recipe.argv), request=request.model_dump(mode="json"),
                                          timeout_seconds=5, storage_mib=16)

    def _finish(self, campaign_id: str, attempt: dict, contract: CampaignContract) -> None:
        recipe, workspace, _ = self._prepare(contract, attempt)
        status = self.driver.wait(attempt["backend_label"], timeout=10)
        attempt = self.controller.reconcile(attempt["id"], lambda _: self._inspection(attempt))
        if not status.stopped or not attempt["stopped_confirmed"]:
            raise DriverError("Job remains uncertain; capacity retained, resume reconciles it")
        if attempt["state"] != "VERIFYING":
            attempt = self.controller.transition(attempt["id"], "VERIFYING", fence=attempt["fence"])
        receipt = self._put({"attempt_id": attempt["id"], "backend_label": attempt["backend_label"],
                             "returncode": status.returncode, "stopped": status.stopped,
                             "stdout": status.stdout.decode("utf-8", errors="replace"),
                             "stderr": status.stderr.decode("utf-8", errors="replace"),
                             "detail": status.detail, "recipe": recipe.identity},
                            producer=f"execution:{attempt['id']}", lineage=[digest(contract), recipe.source])
        raw = None
        try:
            response = validate_response(status.stdout, attempt["id"], status.returncode)
            if len(response.produced_artifacts) != 1 or response.produced_artifacts[0].path != "result.json":
                raise ValueError("Frozen fixture output schema requires exactly result.json")
            # Content-addressed bytes may be identical across campaigns. Their
            # occurrence/provenance lives in the unique observation and receipt,
            # so sharing bytes cannot union unrelated campaign ancestors.
            raw = self.store.ingest(workspace / "output", "result.json", producer="controller:raw-content",
                                    completed=True, media_type="application/json")
            observation = evaluate(kind=contract.controls["kind"], payload=self.store.read(raw["digest"]),
                                   attempt_id=attempt["id"], recipe=recipe.identity, artifact=raw["digest"],
                                   evaluator=parse_json(self.store.read(contract.evaluator)),
                                   data=parse_json(self.store.read(contract.data)),
                                   expected_method=recipe.config["method"])
            verified = self._put(observation.model_dump(mode="json"), producer=f"evaluator:{attempt['id']}",
                                 lineage=[raw["digest"], receipt["digest"], contract.evaluator,
                                          contract.data, contract.analysis],
                                 assurance="independently_recomputed")
            self.controller.record_result(attempt["id"], {"observation": verified["digest"],
                                                          "raw": raw["digest"], "recipe": recipe.identity,
                                                          "execution": receipt["digest"]})
            self.controller.transition(attempt["id"], "SUCCEEDED", fence=attempt["fence"])
        except (ValueError, OSError) as exc:
            current = self.controller.attempt(attempt["id"])
            if current["diagnostic"] is None:
                self.controller.record_diagnostic(attempt["id"], {
                    "execution": receipt["digest"], "recipe": recipe.identity,
                    "raw": raw["digest"] if raw else None, "error": str(exc),
                }, fence=current["fence"])
            self.controller.transition(attempt["id"], "FAILED", fence=current["fence"],
                                       detail={"error_type": type(exc).__name__, "message": str(exc)})

    def run(self, campaign_id: str, approval_id: str) -> dict:
        self._ensure_contract_artifact(campaign_id)
        campaign = self.controller.campaign(campaign_id)
        contract = CampaignContract.model_validate(campaign["contract"])
        if digest(contract) != campaign["digest"]:
            raise ValueError("Persisted contract digest mismatch")
        if campaign["state"] == "COMPLETE":
            return self.report(campaign_id)
        if contract.profile != "development":
            raise PermissionError("Only developer fixtures are enabled by this lab application")
        if campaign["state"] == "FROZEN":
            self.controller.start_confirmation(campaign_id)
        self.agents.execute_ready(campaign_id, approval_id)
        # Reconcile existing attempts first; never substitute new attempt IDs on restart.
        existing = self.controller.attempts(campaign_id)
        for old in existing:
            if old["backend"] == "offline-agent":
                continue
            if old["state"] in TERMINAL and not old["resources_held"]:
                continue
            current = self.controller.reconcile(old["id"], lambda _: self._inspection(old))
            if current["state"] in TERMINAL:
                continue
            if current["launch_permitted"]:
                _, _, spec = self._prepare(contract, current)
                self.controller.authorize_launch(current["id"])
                self.driver.launch(spec)
                current = self.controller.transition(current["id"], "RUNNING", fence=current["fence"])
            if current["state"] != "LOST":
                self._finish(campaign_id, current, contract)
        launched = []
        for task in self.controller.ready_tasks(campaign_id):
            attempt = self.controller.reserve(task["id"], approval_id=approval_id, principal="developer",
                                               backend="development", available_capabilities=["development"])
            try:
                _, _, spec = self._prepare(contract, attempt)
                self.controller.authorize_launch(attempt["id"])
                self.driver.launch(spec)
                attempt = self.controller.transition(attempt["id"], "RUNNING", fence=attempt["fence"])
                launched.append(attempt)
            except (ValueError, OSError, DriverError):
                self.controller.reconcile(attempt["id"], lambda _: self._inspection(attempt))
                raise
        for attempt in launched:
            self._finish(campaign_id, attempt, contract)
        if any(task["state"] not in TERMINAL for task in self.controller.tasks(campaign_id)):
            # A blocked branch retains its state while independently ready work
            # completes. Do not manufacture a terminal campaign diagnosis.
            return self.report(campaign_id)
        results = [r for r in self.controller.results(campaign_id)
                   if "observation" in r["record"]
                   and self.controller.attempt(r["attempt_id"])["state"] == "SUCCEEDED"]
        observations = {r["record"]["recipe"]: (r["record"]["observation"], Observation.model_validate(
            parse_json(self.store.read(r["record"]["observation"])))) for r in results}
        if len(observations) == len(contract.recipes):
            base_id, baseline = observations[contract.baseline]
            treatment_id, treatment = observations[contract.candidates[0]]
            analysis = comparison(baseline, treatment, contract.practical_threshold)
            analysis.update(campaign_id=campaign_id, observations=[base_id, treatment_id])
            artifact = self._put(analysis, producer=f"comparison:{campaign_id}",
                                 lineage=[base_id, treatment_id, contract.analysis, digest(contract),
                                          *[a["result"]["execution"] for a in self.controller.attempts(campaign_id)
                                            if a["backend"] == "offline-agent" and a["result"]]],
                                 assurance="independently_recomputed")
            self.controller.complete(campaign_id, execution_status="succeeded", protocol_status="valid",
                                      finding=analysis["finding"].upper(), evidence_ids=[artifact["digest"]])
        else:
            lineage = [identity for attempt in self.controller.attempts(campaign_id)
                       for record in (attempt["result"], attempt["diagnostic"]) if record
                       for key, identity in record.items()
                       if key in {"raw", "execution", "observation"} and identity is not None]
            diagnostic = self._put({"campaign_id": campaign_id, "execution": "failed",
                                    "validity": "invalid", "finding": "inconclusive",
                                    "attempts": self.controller.attempts(campaign_id)},
                                   producer=f"diagnostic:{campaign_id}",
                                   lineage=[*lineage, campaign["digest"]])
            self.controller.complete(campaign_id, execution_status="failed", protocol_status="invalid",
                                      finding="INCONCLUSIVE", evidence_ids=[diagnostic["digest"]])
        return self.report(campaign_id)

    def report(self, campaign_id: str) -> dict:
        campaign = self.controller.campaign(campaign_id)
        outcome = campaign["outcome"]
        evidence = []
        validity = "incomplete"
        assurance = "unverified"
        if outcome:
            for identity in outcome["evidence_ids"]:
                record = self.store.get(identity)
                analysis = parse_json(self.store.read(identity))
                # Valid, independently recomputed bytes are not this campaign's
                # evidence unless they are actually attributable to it. Borrowed
                # analysis from another campaign cannot certify this one.
                if type(analysis) is not dict:
                    raise ValueError("Outcome evidence must contain a structured outcome")
                attributable = (analysis.get("campaign_id") == campaign_id
                                and campaign["digest"] in record["lineage"])
                consistent = outcome_matches(analysis, execution=outcome["execution_status"],
                                             validity=outcome["protocol_status"], finding=outcome["finding"])
                analysis["assurance"] = record["assurance"]
                if record["status"] != "valid" or not attributable or not consistent:
                    analysis = {**analysis, "validity": "invalid", "finding": "inconclusive",
                                "assurance": "unverified"}
                evidence.append({"digest": identity, "status": record["status"],
                                 "assurance": record["assurance"], "analysis": analysis,
                                 "attributable": attributable, "consistent": consistent})
            validity = (outcome["protocol_status"]
                        if all(e["status"] == "valid" and e["attributable"] and e["consistent"] for e in evidence)
                        else "invalid")
            if validity == "valid":
                assurance = ("independently_recomputed" if all(e["assurance"] == "independently_recomputed" for e in evidence)
                             else "imported" if any(e["assurance"] == "imported" for e in evidence)
                             else "traceable")
        return {"campaign_id": campaign_id, "contract_digest": campaign["digest"],
                "brief": campaign["contract"]["brief"], "state": campaign["state"],
                "execution": outcome["execution_status"] if outcome else "pending", "validity": validity,
                "finding": outcome["finding"].lower() if outcome and assurance == "independently_recomputed" else "inconclusive",
                "assurance": assurance,
                "profile": "development", "adversarial_isolation": False,
                "attempts": self.controller.attempts(campaign_id),
                "budget_reserved": self.controller.budget_used(campaign_id), "evidence": evidence,
                "limitations": ["Trusted synthetic fixtures only; no adversarial isolation",
                                "Finite-domain oracle; no population inference",
                                "No live provider, GPU or deployed-controller assurance"]}

    def cancel(self, campaign_id: str) -> dict:
        for task in self.controller.tasks(campaign_id):
            if task["state"] not in TERMINAL and task["state"] != "ACTIVE":
                self.controller.cancel_task(task["id"], "Developer requested cancellation")
        for attempt in self.controller.attempts(campaign_id):
            if attempt["state"] in TERMINAL and not attempt["resources_held"]:
                continue
            if attempt["backend"] == "offline-agent":
                self.agents.cancel(attempt["id"])
                continue
            status = self.driver.cancel(attempt["backend_label"])
            current = self.controller.reconcile(attempt["id"], lambda _: self._inspection(attempt))
            if status.stopped and current["state"] not in TERMINAL:
                self.controller.transition(current["id"], "CANCELLED", fence=current["fence"])
        campaign = self.controller.campaign(campaign_id)
        attempts = self.controller.attempts(campaign_id)
        tasks = self.controller.tasks(campaign_id)
        if (campaign["state"] in {"FROZEN", "CONFIRMING"}
                and all(task["state"] in TERMINAL for task in tasks)
                and all(a["state"] in TERMINAL and not a["resources_held"] for a in attempts)):
            self._ensure_contract_artifact(campaign_id)
            references = [value for attempt in attempts
                          for record in (attempt["result"], attempt["diagnostic"]) if record
                          for key, value in record.items()
                          if key in {"observation", "raw", "execution"} and value is not None]
            stopped = self._put({"campaign_id": campaign_id, "execution": "cancelled",
                                 "validity": "incomplete", "finding": "inconclusive",
                                 "tasks": tasks, "attempts": attempts},
                                producer=f"cancellation:{campaign_id}",
                                lineage=[*references, campaign["digest"]],
                                historical=True)
            self.controller.complete(campaign_id, execution_status="cancelled", protocol_status="incomplete",
                                      finding="INCONCLUSIVE", evidence_ids=[stopped["digest"]])
        return self.report(campaign_id)

    def export(self, campaign_id: str, destination: Path) -> Path:
        campaign = self.controller.campaign(campaign_id)
        if not campaign["outcome"]:
            raise ValueError("Campaign has no completed evidence packet")
        report = self.report(campaign_id)
        report_record = self._put(report, producer=f"report:{campaign_id}",
                                  lineage=campaign["outcome"]["evidence_ids"], historical=True)
        packet = self._put({"contract": campaign["contract"], "events": self.controller.events(campaign_id),
                   "report": report_record["digest"]}, producer=f"packet:{campaign_id}",
                  lineage=[report_record["digest"], digest(campaign["contract"])], historical=True)
        return self.store.export_bundle(destination, [packet["digest"]])

    def quantitative_claim(self, campaign_id: str, claim: dict) -> dict:
        campaign = self.controller.campaign(campaign_id)
        if not campaign["outcome"] or claim.get("analysis") not in campaign["outcome"]["evidence_ids"]:
            raise ValueError("Claim does not cite this campaign's registered comparison")
        record = self.store.get(claim["analysis"])
        if record["status"] != "valid" or record["assurance"] != "independently_recomputed":
            raise ValueError("Claim evidence is unavailable or lacks independent provenance")
        if self.report(campaign_id)["validity"] != "valid":
            raise ValueError("Campaign does not support a valid quantitative claim")
        checked = validate_quantitative_claim(claim, self.store.read(claim["analysis"]))
        return {**checked, "assurance": "independently_recomputed"}


def demo(root: Path | None = None) -> dict:
    if root is None:
        parent = Path(tempfile.mkdtemp(prefix="kestrel-pilot-", dir=tempfile.gettempdir()))
        root = parent / "lab"
    with Lab.initialize(root) as lab:
        fixture_root = lab.root / "fixtures"
        manifests = generate(fixture_root, framework_root())
        reports = []
        for manifest in manifests:
            record = lab.register(manifest, snapshot_dirty=True)
            campaign_id = lab.propose(record["project_id"],
                                       "Does the predeclared inferior treatment improve the finite synthetic metric?")
            identity = lab.controller.campaign(campaign_id)["digest"]
            approval = lab.approve(campaign_id, identity, (lab.root / "operator.token").read_text())
            reports.append(lab.run(campaign_id, approval))
        packet = lab.export(reports[-1]["campaign_id"], lab.root / "evidence-packet")
        summary = {"root": str(lab.root), "provider_calls": 0, "reports": reports,
                   "evidence_packet": str(packet), "profile": "development"}
        (lab.root / "demo.json").write_bytes(canonical(summary))
        return summary
