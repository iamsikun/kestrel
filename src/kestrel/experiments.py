"""Execution-only experiments over the existing authoritative controller ledger."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .artifacts import _safe_read
from .contracts import Digest, StrictModel, digest, validate_response
from .controller import TERMINAL
from .integration import Connections, Replacement, validate_edits
from .runners import DockerDriver, DriverError, JobSpec


class Limits(StrictModel):
    seconds: int = Field(default=30, ge=1, le=120)
    cpu: int = Field(default=1, ge=1, le=2)
    memory_mib: int = Field(default=256, ge=16, le=512)
    pids: int = Field(default=64, ge=1, le=128)
    storage_mib: int = Field(default=64, ge=1, le=256)
    require_hard_storage: bool = False


class ExperimentContract(StrictModel):
    contract_version: Literal['execution-1'] = 'execution-1'
    project_id: str
    revision: Digest
    snapshot: Digest
    operation: str
    parameters: dict
    edits: list[Replacement] = Field(max_length=64)
    image: str
    limits: Limits
    profile: Literal['isolated-local'] = 'isolated-local'
    capabilities: list[Literal['execute']]
    budget: dict[str, int]
    accepted_findings: list[Literal['inconclusive']]

    @model_validator(mode='after')
    def envelope(self):
        if self.capabilities != ['execute'] or self.accepted_findings != ['inconclusive']:
            raise ValueError('Execution-only capabilities and findings are fixed')
        if self.budget != {'attempts': 1, 'runtime_seconds': self.limits.seconds, 'provider_calls': 0, 'tokens': 0}:
            raise ValueError('Execution budget must match limits')
        return self


class Experiments:
    def __init__(self, lab):
        self.lab = lab
        self.connections = Connections(lab.projects)
        self.controller = lab.controller

    def propose(self, snapshot: str, operation: str, parameters: dict | None = None,
                edits: list[dict] | None = None, limits: dict | None = None) -> dict:
        source = self.connections.get_snapshot(snapshot)
        project = self.connections.revision(source['project_id'], source['revision'])
        if project.classification != 'public_synthetic':
            raise ValueError('This construction profile permits only public synthetic experiments')
        if operation not in project.operations:
            raise ValueError('Unsupported operation')
        resolved = project.operations[operation].resolve(parameters or {})
        replacements = [Replacement.model_validate(e) for e in (edits or [])]
        validate_edits(project, source, replacements)
        bounds = Limits.model_validate(limits or {})
        contract = ExperimentContract(project_id=source['project_id'], revision=source['revision'],
            snapshot=snapshot, operation=operation, parameters=resolved, edits=replacements,
            image=project.image, limits=bounds, capabilities=['execute'], accepted_findings=['inconclusive'],
            budget={'attempts': 1, 'runtime_seconds': bounds.seconds, 'provider_calls': 0, 'tokens': 0})
        # Archive all selected bytes, including the proposed patch, for attributable export.
        inputs = []
        for entry in source['files']:
            data = _safe_read(Path(source['snapshot_path']), entry['path'], self.lab.projects.max_source_bytes)
            inputs.append(self.lab.store.put_bytes(data, producer='controller:source-content')['digest'])
        inputs = [self.lab._put({'source_files': inputs[i:i + 512]},
                                producer='controller:source-index', lineage=inputs[i:i + 512])['digest']
                  for i in range(0, len(inputs), 512)]
        self.lab._put({k: v for k, v in source.items() if k not in ('digest', 'snapshot_path')},
                      producer='controller:selected-source', lineage=inputs)
        self.lab._put(project, producer='controller:project-contract')
        self.lab._put(contract, producer='controller:execution-contract', lineage=[snapshot, source['revision']])
        campaign = self.controller.propose(contract.model_dump(mode='json'))
        self.controller.add_task(campaign, {'recipe': digest(contract), 'profile': 'isolated-local',
            'operation': 'execute', 'budget': {'runtime_seconds': bounds.seconds, 'provider_calls': 0, 'tokens': 0},
            'resources': {'cpu': bounds.cpu}})
        self.controller.freeze(campaign)
        return self.inspect(campaign)

    def _contract(self, identity: str) -> ExperimentContract:
        campaign = self.controller.campaign(identity)
        contract = ExperimentContract.model_validate(campaign['contract'])
        if digest(contract) != campaign['digest'] or self.lab.store.get(campaign['digest'])['status'] != 'valid':
            raise ValueError('Experiment contract integrity failure')
        self.lab.store.read(campaign['digest'])
        return contract

    def inspect(self, identity: str) -> dict:
        contract = self._contract(identity)
        campaign = self.controller.campaign(identity)
        attempts = self.controller.attempts(identity)
        from .contracts import parse_json
        for attempt in attempts:
            record = attempt['result'] or attempt['diagnostic']
            attempt['execution_success'] = None
            attempt['protocol_valid'] = None
            if record and 'execution' in record:
                receipt = parse_json(self.lab.store.read(record['execution']))
                attempt['execution_success'] = receipt['returncode'] == 0 and receipt['stopped']
                attempt['protocol_valid'] = attempt['result'] is not None
        from .reporting import verify_outcome_evidence
        _, validity, assurance = verify_outcome_evidence(identity, campaign, self.lab.store)
        return {'protocol_validity': validity, 'evidence_assurance': assurance, 'experiment_id': identity, 'digest': campaign['digest'], 'state': campaign['state'],
                'contract': contract.model_dump(mode='json'), 'attempts': attempts,
                'budget_charged': self.controller.budget_used(identity), 'outcome': campaign['outcome'],
                'scientific_outcome': 'not_evaluated', 'metrics_assurance': 'self_reported',
                'next_command': f'kestrel --lab LAB experiment approve {identity} --digest {campaign["digest"]} --operator-token-file TOKEN'}

    def approve(self, identity: str, expected: str, token: str) -> str:
        self._contract(identity)
        return self.lab.approve(identity, expected, token)

    def _driver(self, contract: ExperimentContract) -> DockerDriver:
        return DockerDriver(contract.image, self.lab.projects.candidates)

    def _workspace(self, contract: ExperimentContract, attempt: dict) -> Path:
        source = self.connections.get_snapshot(contract.snapshot)
        project = self.connections.revision(contract.project_id, contract.revision)
        if source['project_id'] != contract.project_id or source['revision'] != contract.revision or project.image != contract.image:
            raise ValueError('Execution binding mismatch')
        if project.operations[contract.operation].resolve(contract.parameters) != contract.parameters:
            raise ValueError('Parameter binding mismatch')
        changes = validate_edits(project, source, contract.edits)
        target = self.lab.projects.candidates / attempt['backend_label']
        if target.exists() or target.is_symlink():
            expected = []
            for entry in source['files']:
                item = dict(entry)
                if entry['path'] in changes:
                    data = changes[entry['path']]
                    item.update(digest=hashlib.sha256(data).hexdigest(), size=len(data))
                expected.append(item)
            self.lab.projects._verify_snapshot(target, expected, source['directories'])
            return target
        temporary = Path(tempfile.mkdtemp(prefix='.experiment-', dir=self.lab.projects.candidates))
        try:
            for directory in source['directories']:
                (temporary / directory).mkdir(parents=True, exist_ok=True)
            total = 0
            for entry in source['files']:
                data = changes.get(entry['path'])
                if data is None:
                    data = _safe_read(Path(source['snapshot_path']), entry['path'], self.lab.projects.max_source_bytes)
                total += len(data)
                if total > self.lab.projects.max_source_bytes:
                    raise ValueError('Candidate source exceeds byte limit')
                path = temporary / entry['path']
                path.write_bytes(data)
                path.chmod(0o755 if entry['executable'] else 0o644)
            os.rename(temporary, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return target

    def _spec(self, contract: ExperimentContract, attempt: dict, workspace: Path) -> JobSpec:
        project = self.connections.revision(contract.project_id, contract.revision)
        bounds = contract.limits
        return JobSpec(attempt_id=attempt['backend_label'], workspace=workspace,
            argv=tuple(project.operations[contract.operation].argv),
            request={'protocol_version': '0.1', 'task_id': attempt['task_id'], 'attempt_id': attempt['id'],
                     'contract_digest': digest(contract), 'operation': contract.operation,
                     'input_artifacts': [], 'output_directory': 'output', 'operation_parameters': contract.parameters},
            timeout_seconds=bounds.seconds, cpu=bounds.cpu, memory_mib=bounds.memory_mib,
            pids=bounds.pids, storage_mib=bounds.storage_mib, required_profile='isolated-local',
            require_hard_storage=bounds.require_hard_storage)

    def _reconcile(self, driver, attempt):
        status = driver.reconcile(attempt['backend_label'])
        return self.controller.reconcile(attempt['id'], lambda _: {
            'backend_label': attempt['backend_label'], 'status': status.state,
            'never_launched': status.state == 'absent'})

    def _finish(self, identity, contract, driver, attempt):
        # Poll in short intervals: another client can cancel; restart uses the same attempt.
        deadline = time.monotonic() + contract.limits.seconds + 15
        status = driver.reconcile(attempt['backend_label'])
        while status.state in ('running', 'unknown') and time.monotonic() < deadline:
            time.sleep(0.1)
            status = driver.reconcile(attempt['backend_label'])
        current = self._reconcile(driver, attempt)
        if current['state'] in TERMINAL:
            return
        if not status.stopped or not current['stopped_confirmed']:
            raise DriverError('Attempt uncertain; reservation retained; rerun to reconcile')
        if current['state'] != 'VERIFYING':
            current = self.controller.transition(current['id'], 'VERIFYING', fence=current['fence'])
        receipt = self.lab._put({'attempt_id': current['id'], 'campaign_id': identity,
            'returncode': status.returncode, 'stopped': status.stopped,
            'stdout': status.stdout.decode(errors='replace'), 'stderr': status.stderr.decode(errors='replace'),
            'detail': status.detail}, producer=f'execution:{current["id"]}', lineage=[digest(contract)])
        outputs = []
        try:
            response = validate_response(status.stdout, current['id'], status.returncode)
            project = self.connections.revision(contract.project_id, contract.revision)
            for artifact in response.produced_artifacts:
                if artifact.media_type not in project.operations[contract.operation].artifact_types:
                    raise ValueError('Undeclared artifact type')
                output = self.lab.store.ingest(self.lab.projects.candidates / current['backend_label'] / 'output',
                    artifact.path, producer='controller:raw-content', completed=True, media_type=artifact.media_type)
                outputs.append({'path': artifact.path, 'digest': output['digest']})
            occurrence = self.lab._put({'attempt_id': current['id'], 'campaign_id': identity,
                'outputs': outputs, 'diagnostics': response.diagnostics, 'assurance': 'self_reported'},
                producer=f'outputs:{current["id"]}', lineage=[receipt['digest'], *[o['digest'] for o in outputs]])
            self.controller.record_result(current['id'], {'execution': receipt['digest'], 'outputs': occurrence['digest']})
            self.controller.transition(current['id'], 'SUCCEEDED', fence=current['fence'])
        except (ValueError, OSError) as exc:
            self.controller.record_diagnostic(current['id'], {'execution': receipt['digest'], 'error': str(exc)}, fence=current['fence'])
            self.controller.transition(current['id'], 'FAILED', fence=current['fence'])

    def _complete(self, identity: str, cancelled=False):
        campaign = self.controller.campaign(identity)
        if campaign['state'] == 'COMPLETE':
            return
        tasks, attempts = self.controller.tasks(identity), self.controller.attempts(identity)
        if any(t['state'] not in TERMINAL for t in tasks) or any(a['resources_held'] or a['state'] not in TERMINAL for a in attempts):
            return
        success = bool(tasks) and all(t['state'] == 'SUCCEEDED' for t in tasks)
        execution = 'succeeded' if success else ('cancelled' if cancelled else 'failed')
        validity = 'valid' if success else ('incomplete' if cancelled else 'invalid')
        references = [v for a in attempts for r in (a['result'], a['diagnostic']) if r
                      for k, v in r.items() if k in ('execution', 'outputs')]
        packet = self.lab._put({'campaign_id': identity, 'execution': execution, 'validity': validity,
            'finding': 'inconclusive', 'scientific_outcome': 'not_evaluated', 'attempts': attempts},
            producer=f'experiment:{identity}', lineage=[campaign['digest'], *references])
        self.controller.complete(identity, execution_status=execution, protocol_status=validity,
                                 finding='INCONCLUSIVE', evidence_ids=[packet['digest']])

    def run(self, identity: str, approval: str) -> dict:
        contract = self._contract(identity)
        if self.controller.campaign(identity)['state'] == 'COMPLETE':
            return self.inspect(identity)
        driver = self._driver(contract)
        if contract.limits.require_hard_storage or not driver.probe().get('available'):
            raise DriverError('Requested enforcement or pinned local image unavailable; no launch')
        if self.controller.campaign(identity)['state'] == 'FROZEN':
            self.controller.start_confirmation(identity)
        for old in self.controller.attempts(identity):
            if old['state'] in TERMINAL and not old['resources_held']:
                continue
            current = self._reconcile(driver, old)
            if current['launch_permitted']:
                # A prepared-but-undispatched workspace is never silently trusted.
                workspace = self._workspace(contract, current)
                self.controller.authorize_launch(current['id'])
                driver.launch(self._spec(contract, current, workspace))
                current = self.controller.transition(current['id'], 'RUNNING', fence=current['fence'])
            if current['state'] not in TERMINAL:
                self._finish(identity, contract, driver, current)
        for task in self.controller.ready_tasks(identity):
            attempt = self.controller.reserve(task['id'], approval_id=approval, principal='developer',
                backend='docker', available_capabilities=['isolated-local'])
            workspace = self._workspace(contract, attempt)
            self.controller.authorize_launch(attempt['id'])
            driver.launch(self._spec(contract, attempt, workspace))
            attempt = self.controller.transition(attempt['id'], 'RUNNING', fence=attempt['fence'])
            self._finish(identity, contract, driver, attempt)
        self._complete(identity)
        return self.inspect(identity)

    def cancel(self, identity: str) -> dict:
        contract = self._contract(identity)
        driver = self._driver(contract)
        for task in self.controller.tasks(identity):
            if task['state'] not in TERMINAL and task['state'] != 'ACTIVE':
                self.controller.cancel_task(task['id'], 'Operator cancellation')
        for attempt in self.controller.attempts(identity):
            if attempt['state'] in TERMINAL and not attempt['resources_held']:
                continue
            status = driver.cancel(driver._name(attempt['backend_label']))
            current = self._reconcile(driver, attempt)
            if status.stopped and current['state'] not in TERMINAL:
                self.controller.transition(current['id'], 'CANCELLED', fence=current['fence'])
        self._complete(identity, cancelled=True)
        return self.inspect(identity)

    def artifacts(self, identity: str) -> list[dict]:
        from .contracts import parse_json
        self._contract(identity)
        result = []
        for attempt in self.controller.attempts(identity):
            if attempt['result']:
                if self.lab.store.get(attempt['result']['outputs'])['status'] != 'valid':
                    raise ValueError('Output evidence invalidated')
                occurrence = parse_json(self.lab.store.read(attempt['result']['outputs']))
                for output in occurrence['outputs']:
                    self.lab.store.read(output['digest'])
                    result.append({'attempt_id': attempt['id'], **output})
        return result

    def export(self, identity: str, destination: Path) -> Path:
        self._contract(identity)
        campaign = self.controller.campaign(identity)
        if not campaign['outcome']:
            raise ValueError('Experiment must finish before export')
        return self.lab.store.export_bundle(destination, campaign['outcome']['evidence_ids'])
