import hashlib
import json
from pathlib import Path

import pytest
import yaml

from kestrel.application import Lab
from kestrel.cli import main
from kestrel.experiments import Experiments
from kestrel.integration import (
    Connections,
    Replacement,
    initialize,
    load_contract,
    matches,
    validate_edits,
)
from kestrel.integration_examples import generate
from kestrel.projects import ProjectError

IMAGE = 'python@sha256:' + '1' * 64


@pytest.fixture
def connected(tmp_path):
    sources = generate(tmp_path / 'sources', IMAGE)
    with Lab.initialize(tmp_path / 'lab') as lab:
        connections = Connections(lab.projects)
        for path in sources:
            connections.add(path)
        yield lab, connections, sources


def tree(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob('*') if p.is_file()}


def test_two_projects_revision_snapshots_and_proposals(connected):
    lab, connections, sources = connected
    originals = [tree(p) for p in sources]
    assert [r['capabilities'] for r in connections.list()] == [['calculate'], ['repeat']]
    experiments = Experiments(lab)
    for source, operation in zip(sources, ('calculate', 'repeat')):
        preview = connections.preview(source.name)
        assert not preview['blockers']
        snapshot = connections.snapshot(source.name, preview['selection_digest'])
        assert connections.get_snapshot(snapshot['digest']) == snapshot
        edits = [{'path': 'program.py', 'original': originals[sources.index(source)]['program.py'],
                  'content': (source / 'program.py').read_text().replace('FACTOR = 2', 'FACTOR = 4')}]
        proposal = experiments.propose(snapshot['digest'], operation, {'value': 5}, edits)
        assert proposal['budget_charged']['attempts'] == 0
        assert proposal['scientific_outcome'] == 'not_evaluated'
        before = connections.describe(source.name)
        path = source / '.kestrel/project.yaml'
        contract = yaml.safe_load(path.read_text())
        contract['purpose'] += ' refreshed'
        path.write_text(yaml.safe_dump(contract))
        with pytest.raises(ProjectError, match='refresh'):
            connections.preview(source.name)
        assert connections.refresh(source.name)['revision'] != before['revision']
        assert connections.revision(source.name, before['revision'])
        assert experiments.inspect(proposal['experiment_id'])['contract']['revision'] == before['revision']
        # Only our explicit refresh fixture edit changed the source.
        after = tree(source)
        after['.kestrel/project.yaml'] = originals[sources.index(source)]['.kestrel/project.yaml']
        assert after == originals[sources.index(source)]


@pytest.mark.parametrize('change', [
    lambda c: c.update(version='2'), lambda c: c.update(unexpected=True),
    lambda c: c['source'].update(include=['../secret']),
    lambda c: c['source'].update(exclude=['.kestrel/**']),
    lambda c: c.update(image='python:latest'),
    lambda c: c['operations']['calculate']['parameters']['value'].update(default=True),
    lambda c: c['references'].update(architecture=['/etc/passwd']),
])
def test_invalid_contracts(connected, change):
    _, _, sources = connected
    path = sources[0] / '.kestrel/project.yaml'
    value = yaml.safe_load(path.read_text())
    change(value)
    path.write_text(yaml.safe_dump(value))
    with pytest.raises(ValueError):
        load_contract(sources[0])


@pytest.mark.parametrize('value', [0, 11, True, '3', 1.5, None])
def test_parameter_bounds(connected, value):
    _, connections, _ = connected
    contract = load_contract(Path(connections.describe('arithmetic')['source_root']))
    with pytest.raises(ValueError):
        contract.operations['calculate'].resolve({'value': value})


def test_snapshot_stale_protected_edits_and_collisions(connected):
    lab, connections, sources = connected
    with pytest.raises(ProjectError):
        connections.add(sources[0], 'new-name')
    before = connections.preview('arithmetic')
    (sources[0] / 'program.py').write_text('changed')
    with pytest.raises(ProjectError):
        connections.snapshot('arithmetic', before['selection_digest'])
    preview = connections.preview('arithmetic')
    snapshot = connections.snapshot('arithmetic', preview['selection_digest'])
    contract = load_contract(sources[0])
    for path, original in [('program.py', '0' * 64), ('.kestrel/adapter.py', '0' * 64), ('other.py', '0' * 64), ('../program.py', '0' * 64)]:
        with pytest.raises(ProjectError):
            validate_edits(contract, snapshot, [Replacement(path=path, original=original, content='x')])
    assert not lab.controller.attempts('nonexistent')


def test_hostile_project_does_not_execute(connected, monkeypatch):
    _, connections, sources = connected
    import subprocess
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('Onboarding executed subprocess'))
    source = sources[0]
    (source / '.git').mkdir()
    (source / '.git/config').write_text('[core]\nfsmonitor = touch /tmp/forbidden\n')
    (source / 'setup.py').write_text('raise RuntimeError("must not run")')
    (source / '.kestrel/adapter.py').write_text('raise RuntimeError("must not run")')
    preview = connections.preview('arithmetic')
    assert '.git' in preview['excluded']
    assert 'setup.py' in preview['excluded']
    connections.snapshot('arithmetic', preview['selection_digest'])


def test_selected_links_lfs_and_nested_repositories(connected):
    _, connections, sources = connected
    path = sources[0] / 'program.py'
    path.unlink()
    path.symlink_to('/etc/passwd')
    assert connections.preview('arithmetic')['blockers']
    path.unlink()
    path.write_text('version https://git-lfs.github.com/spec/v1\n')
    assert connections.preview('arithmetic')['blockers']
    path.write_text('x')
    (sources[0] / 'nested/.git').mkdir(parents=True)
    assert connections.preview('arithmetic')['blockers']


def test_init_no_overwrite_and_cli(tmp_path, capsys):
    path = tmp_path / 'new'
    path.mkdir()
    assert main(['project', 'init', str(path), '--json']) == 0
    assert json.loads(capsys.readouterr().out)['setup_requirements']
    before = tree(path)
    with pytest.raises(FileExistsError):
        initialize(path)
    assert tree(path) == before
    with pytest.raises(ValueError):
        load_contract(path)


def test_globs():
    assert matches('a.py', '**/*.py')
    assert matches('a/b.py', '**/*.py')
    assert not matches('a/b.py', '*.py')
    assert matches('data', 'data/**')


def test_race_during_snapshot(connected, monkeypatch):
    _, connections, sources = connected
    preview = connections.preview('arithmetic')
    scan = connections._scan
    calls = 0

    def racing(name):
        nonlocal calls
        calls += 1
        if calls == 2:
            (sources[0] / 'program.py').write_text('concurrent change')
        return scan(name)
    monkeypatch.setattr(connections, '_scan', racing)
    with pytest.raises(ProjectError, match='changed'):
        connections.snapshot('arithmetic', preview['selection_digest'])
    assert not connections.db.execute('SELECT 1 FROM selected_snapshots').fetchone()


@pytest.mark.isolation
def test_actual_two_project_experiments(tmp_path):
    import os
    if os.environ.get('KESTREL_RUN_ISOLATION') != '1':
        pytest.skip('Actual Docker integration gate requires KESTREL_RUN_ISOLATION=1')
    image = 'ghcr.io/astral-sh/uv@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca'
    sources = generate(tmp_path / 'sources', image)
    originals = [tree(path) for path in sources]
    with Lab.initialize(tmp_path / 'lab') as lab:
        service = Experiments(lab)
        try:
            for source, operation, expected in zip(sources, ('calculate', 'repeat'), (20, 'sample' * 5)):
                service.connections.add(source)
                preview = service.connections.preview(source.name)
                snapshot = service.connections.snapshot(source.name, preview['selection_digest'])
                edits = [{'path': 'program.py', 'original': tree(source)['program.py'],
                          'content': (source / 'program.py').read_text().replace('FACTOR = 2', 'FACTOR = 4')}]
                proposal = service.propose(snapshot['digest'], operation, {'value': 5}, edits)
                identity = proposal['experiment_id']
                approval = service.approve(identity, proposal['digest'], (lab.root / 'operator.token').read_text())
                result = service.run(identity, approval)
                assert result['outcome']['execution_status'] == 'succeeded', result
                assert result['outcome']['protocol_status'] == 'valid'
                assert result['scientific_outcome'] == 'not_evaluated'
                artifacts = service.artifacts(identity)
                assert json.loads(lab.store.read(artifacts[0]['digest']))['result'] == expected
                service.export(identity, tmp_path / f'{source.name}.zip')
                assert service.run(identity, approval)['budget_charged']['attempts'] == 1
            assert [tree(path) for path in sources] == originals
        finally:
            for row in lab.controller._db.execute('SELECT id FROM campaigns').fetchall():
                contract = service._contract(row[0])
                driver = service._driver(contract)
                for attempt in lab.controller.attempts(row[0]):
                    driver.cancel(driver._name(attempt['backend_label']))
                    driver.remove(driver._name(attempt['backend_label']))


@pytest.mark.isolation
@pytest.mark.parametrize('scenario', ['malformed', 'traversal', 'timeout', 'cancel', 'restart'])
def test_actual_experiment_failures_and_recovery(tmp_path, monkeypatch, scenario):
    import os
    if os.environ.get('KESTREL_RUN_ISOLATION') != '1':
        pytest.skip('Actual Docker integration gate requires KESTREL_RUN_ISOLATION=1')
    image = 'ghcr.io/astral-sh/uv@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca'
    source = generate(tmp_path / 'sources', image)[0]
    adapter = source / '.kestrel/adapter.py'
    if scenario == 'malformed':
        adapter.write_text('print("not JSON")\n')
    elif scenario == 'traversal':
        adapter.write_text('import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({"protocol_version":"0.1","attempt_id":r["attempt_id"],"status":"success","produced_artifacts":[{"path":"../program.py","media_type":"text/plain","complete":True}],"diagnostics":{}}))\n')
        manifest = source / '.kestrel/project.yaml'
        value = yaml.safe_load(manifest.read_text())
        value['operations']['calculate']['artifact_types'].append('text/plain')
        manifest.write_text(yaml.safe_dump(value))
    elif scenario in ('timeout', 'cancel'):
        adapter.write_text('import time\ntime.sleep(30)\n')
    original = tree(source)
    with Lab.initialize(tmp_path / 'lab') as lab:
        service = Experiments(lab)
        service.connections.add(source)
        preview = service.connections.preview(source.name)
        snapshot = service.connections.snapshot(source.name, preview['selection_digest'])
        proposal = service.propose(snapshot['digest'], 'calculate', limits={'seconds': 1 if scenario == 'timeout' else 10})
        identity = proposal['experiment_id']
        approval = service.approve(identity, proposal['digest'], (lab.root / 'operator.token').read_text())
        driver = service._driver(service._contract(identity))
        try:
            if scenario in ('cancel', 'restart'):
                def interrupt(*args):
                    raise InterruptedError('synthetic controller interruption after launch')
                monkeypatch.setattr(service, '_finish', interrupt)
                with pytest.raises(InterruptedError):
                    service.run(identity, approval)
                attempt_id = lab.controller.attempts(identity)[0]['id']
                # Reopen all authoritative stores and construct a fresh driver.
                lab.close()
                lab = Lab(tmp_path / 'lab')
                service = Experiments(lab)
                if scenario == 'cancel':
                    lab.store.invalidate(snapshot['digest'], 'synthetic invalidation while running')
                result = service.cancel(identity) if scenario == 'cancel' else service.run(identity, approval)
                assert result['attempts'][0]['id'] == attempt_id
                assert result['outcome']['execution_status'] == ('cancelled' if scenario == 'cancel' else 'succeeded')
            else:
                result = service.run(identity, approval)
                assert result['outcome']['protocol_status'] == 'invalid'
                if scenario in ('malformed', 'traversal'):
                    assert result['attempts'][0]['execution_success'] is True
                    assert result['attempts'][0]['protocol_valid'] is False
            assert result['budget_charged']['attempts'] == 1
            assert not result['attempts'][0]['resources_held']
            assert tree(source) == original
        finally:
            for attempt in lab.controller.attempts(identity):
                driver.cancel(driver._name(attempt['backend_label']))
                driver.remove(driver._name(attempt['backend_label']))
            # In restart scenarios this is a second connection, not the context's original.
            if scenario in ('cancel', 'restart'):
                lab.close()


def test_execution_reporting_and_unavailable_enforcement(connected, monkeypatch):
    lab, connections, _ = connected
    from kestrel.briefings import brief
    from kestrel.runners import DriverError
    preview = connections.preview('arithmetic')
    snapshot = connections.snapshot('arithmetic', preview['selection_digest'])
    service = Experiments(lab)
    proposal = service.propose(snapshot['digest'], 'calculate', limits={'require_hard_storage': True})
    identity = proposal['experiment_id']
    assert lab.report(identity)['scientific_outcome'] == 'not_evaluated'
    assert brief(lab.root, form='json')
    with pytest.raises(DriverError, match='unavailable'):
        service.run(identity, 'no-approval')
    assert not lab.controller.attempts(identity)
    assert service.cancel(identity)['outcome']['execution_status'] == 'cancelled'


def test_invalidated_inputs_block_launch_but_allow_cancel(connected):
    lab, connections, _ = connected
    preview = connections.preview('arithmetic')
    snapshot = connections.snapshot('arithmetic', preview['selection_digest'])
    service = Experiments(lab)
    proposal = service.propose(snapshot['digest'], 'calculate')
    identity = proposal['experiment_id']
    lab.store.invalidate(snapshot['digest'], 'synthetic invalidation')
    with pytest.raises(ValueError, match='invalidated'):
        service.run(identity, 'unused')
    result = service.cancel(identity)
    assert result['outcome']['execution_status'] == 'cancelled'
    assert result['protocol_validity'] == 'invalid'
    assert result['budget_charged']['attempts'] == 0
