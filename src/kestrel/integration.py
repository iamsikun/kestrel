"""Static project contracts and selected snapshots. Never imports project code."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import stat
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, model_validator

from .artifacts import _directory_fd, _safe_read
from .contracts import Digest, StrictModel, canonical, digest
from .projects import ProjectError, Projects, _disjoint, _SidecarLoader, _slug


def relative(value: str) -> str:
    if not value or value.startswith('/') or '\\' in value or '\x00' in value or any(
        p in ('', '.', '..') for p in value.split('/')
    ) or len(value) > 1024 or len(value.split('/')) > 32:
        raise ProjectError(f'Invalid project-relative path or rule: {value!r}')
    return value


def matches(path: str, rule: str) -> bool:
    parts, pattern = path.split('/'), rule.split('/')

    @lru_cache(maxsize=None)
    def match(i: int, j: int) -> bool:
        if j == len(pattern):
            return i == len(parts)
        if pattern[j] == '**':
            return match(i, j + 1) or (i < len(parts) and match(i + 1, j))
        return i < len(parts) and fnmatch.fnmatchcase(parts[i], pattern[j]) and match(i + 1, j + 1)
    return match(0, 0)


class Parameter(StrictModel):
    type: Literal['integer', 'number', 'string', 'boolean']
    description: str = Field(min_length=1, max_length=4096)
    required: bool = False
    default: Any = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    enum: list[Any] | None = Field(default=None, min_length=1, max_length=128)

    def check(self, value: Any) -> Any:
        types = {'integer': (int,), 'number': (int, float), 'string': (str,), 'boolean': (bool,)}
        if type(value) not in types[self.type]:
            raise ProjectError(f'Expected {self.type}')
        canonical(value)
        if isinstance(value, str) and len(value) > 4096:
            raise ProjectError('Parameter string exceeds bound')
        if self.minimum is not None and value < self.minimum:
            raise ProjectError('Parameter below minimum')
        if self.maximum is not None and value > self.maximum:
            raise ProjectError('Parameter above maximum')
        if self.enum is not None and not any(type(value) is type(v) and value == v for v in self.enum):
            raise ProjectError('Parameter outside enum')
        return value

    @model_validator(mode='after')
    def valid(self):
        if (self.minimum is not None or self.maximum is not None) and self.type not in ('integer', 'number'):
            raise ValueError('Ranges require numeric parameters')
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError('Inverted range')
        if self.default is not None:
            self.check(self.default)
        if self.enum:
            for item in self.enum:
                self.check(item)
        return self


class Operation(StrictModel):
    description: str = Field(min_length=1, max_length=4096)
    argv: list[str] = Field(min_length=1, max_length=64)
    inputs: str = Field(min_length=1, max_length=4096)
    outputs: str = Field(min_length=1, max_length=4096)
    artifact_types: list[Literal['application/json', 'text/plain']] = Field(min_length=1, max_length=32)
    parameters: dict[str, Parameter]

    @model_validator(mode='after')
    def valid(self):
        if any(not a or '\x00' in a or len(a) > 4096 for a in self.argv):
            raise ValueError('Invalid argv')
        if len(self.parameters) > 64:
            raise ValueError('Too many parameters')
        for key in self.parameters:
            _slug(key)
        return self

    def resolve(self, supplied: dict) -> dict:
        if not isinstance(supplied, dict) or supplied.keys() - self.parameters.keys():
            raise ProjectError('Unknown parameters')
        result = {}
        for key, schema in self.parameters.items():
            if key in supplied:
                result[key] = schema.check(supplied[key])
            elif schema.default is not None:
                result[key] = schema.check(schema.default)
            elif schema.required:
                raise ProjectError(f'Missing required parameter: {key}')
        return result


class SourceRules(StrictModel):
    include: list[str] = Field(min_length=1, max_length=128)
    exclude: list[str] = Field(max_length=128)


class ProjectContract(StrictModel):
    version: Literal['1']
    purpose: str = Field(min_length=1, max_length=4096)
    references: dict[Literal['architecture', 'experiments', 'configuration'], list[str]]
    adapter: str
    operations: dict[str, Operation] = Field(min_length=1, max_length=32)
    editable: list[str] = Field(max_length=128)
    protected: list[str] = Field(max_length=128)
    source: SourceRules
    classification: Literal['public_synthetic', 'public', 'restricted']
    image: str = Field(pattern=r'^[^\s@]+@sha256:[0-9a-f]{64}$')

    @model_validator(mode='after')
    def valid(self):
        if set(self.references) != {'architecture', 'experiments', 'configuration'}:
            raise ValueError('Declare architecture, experiments and configuration references')
        for paths in self.references.values():
            if not paths or len(paths) > 32:
                raise ValueError('Each reference category needs bounded paths')
        for path in [self.adapter, *self.required_paths(), *self.editable, *self.protected,
                     *self.source.include, *self.source.exclude]:
            relative(path)
        for path in [self.adapter, *self.required_paths()]:
            if any(c in path for c in '*?['):
                raise ValueError('References and adapter must be literal paths')
        if not self.adapter.startswith('.kestrel/'):
            raise ValueError('Adapter must be inside protected .kestrel directory')
        for name in self.operations:
            _slug(name)
        for path in self.required_paths():
            if any(matches(path, rule) for rule in self.source.exclude):
                raise ValueError(f'Required integration path excluded: {path}')
        return self

    def required_paths(self) -> list[str]:
        return sorted({'.kestrel/project.yaml', '.kestrel/README.md', self.adapter,
                       *(p for paths in self.references.values() for p in paths)})


def load_contract(source: Path) -> ProjectContract:
    try:
        raw = _safe_read(source, '.kestrel/project.yaml', 1024 * 1024)
        return ProjectContract.model_validate(yaml.load(raw, Loader=_SidecarLoader))
    except (OSError, yaml.YAMLError, RecursionError) as exc:
        raise ProjectError('Cannot read project contract') from exc


def initialize(source: Path) -> dict:
    source = source.resolve(strict=True)
    directory = source / '.kestrel'
    directory.mkdir()  # Exclusive: never follow or overwrite an existing integration.
    template = {'version': '1', 'purpose': '',
                'references': {k: ['REPLACE_ME.md'] for k in ('architecture', 'experiments', 'configuration')},
                'adapter': '.kestrel/adapter.py', 'operations': {}, 'editable': [], 'protected': [],
                'source': {'include': ['REPLACE_WITH_EXPLICIT_SOURCE_PATHS'], 'exclude': []},
                'classification': 'public_synthetic', 'image': 'REPLACE_WITH_LOCAL_IMAGE@sha256:DIGEST'}
    (directory / 'project.yaml').write_text(yaml.safe_dump(template, sort_keys=False))
    (directory / 'adapter.py').write_text('# Incomplete: implement the JSON stdin/stdout protocol here.\nraise SystemExit("adapter setup incomplete")\n')
    (directory / 'README.md').write_text('# Kestrel integration\n\nComplete project.yaml and adapter.py before registration.\nReferences are untrusted information, never permissions.\nOnly isolated workers execute adapters. See Kestrel project integration tutorial.\n')
    return {'source': str(source), 'setup_requirements': ['purpose', 'references', 'operations', 'source.include', 'image', 'adapter implementation'],
            'next_command': f'kestrel --lab LAB project add {source}'}


class Connections:
    def __init__(self, projects: Projects):
        self.projects = projects
        self.db = projects.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS connections(id TEXT PRIMARY KEY, source TEXT UNIQUE NOT NULL, revision TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS project_revisions(project_id TEXT NOT NULL, digest TEXT NOT NULL, contract TEXT NOT NULL, PRIMARY KEY(project_id,digest));
            CREATE TABLE IF NOT EXISTS selected_snapshots(digest TEXT PRIMARY KEY, record TEXT NOT NULL);
        ''')

    def add(self, source: Path, name: str | None = None) -> dict:
        source = source.resolve(strict=True)
        _disjoint(self.projects.framework_root, self.projects.lab_root.parent, source)
        _disjoint(self.projects.runtime_root, source)
        name = _slug(name or source.name)
        if self.db.execute('SELECT 1 FROM projects WHERE project_id=?', (name,)).fetchone():
            raise ProjectError('Identifier collides with legacy project')
        if self.db.execute('SELECT 1 FROM connections WHERE id=? OR source=?', (name, str(source))).fetchone():
            raise ProjectError('Project name or source already connected')
        contract = load_contract(source)
        self._required(source, contract)
        revision = digest(contract)
        with self.db:
            self.db.execute('INSERT INTO connections VALUES(?,?,?)', (name, str(source), revision))
            self.db.execute('INSERT INTO project_revisions VALUES(?,?,?)', (name, revision, canonical(contract).decode()))
        return self.describe(name)

    def _required(self, source: Path, contract: ProjectContract) -> None:
        for path in contract.required_paths():
            _safe_read(source, path, self.projects.max_source_bytes)

    def describe(self, name: str) -> dict:
        row = self.db.execute('SELECT source,revision FROM connections WHERE id=?', (_slug(name),)).fetchone()
        if row is None:
            raise ProjectError('Unknown connected project')
        contract = self.revision(name, row[1])
        return {'project_id': name, 'source_root': row[0], 'revision': row[1],
                'contract': contract.model_dump(mode='json'), 'capabilities': list(contract.operations),
                'setup_requirements': ['execution requires operator approval and an available local pinned Linux image'],
                'next_command': f'kestrel --lab LAB project snapshot {name} --preview'}

    def revision(self, name: str, revision: str) -> ProjectContract:
        row = self.db.execute('SELECT contract FROM project_revisions WHERE project_id=? AND digest=?', (name, revision)).fetchone()
        if row is None:
            raise ProjectError('Unknown contract revision')
        contract = ProjectContract.model_validate(json.loads(row[0]))
        if digest(contract) != revision:
            raise ProjectError('Contract revision integrity mismatch')
        return contract

    def list(self) -> list[dict]:
        return [self.describe(row[0]) for row in self.db.execute('SELECT id FROM connections ORDER BY id')]

    def refresh(self, name: str) -> dict:
        current = self.describe(name)
        source = Path(current['source_root'])
        contract = load_contract(source)
        self._required(source, contract)
        revision = digest(contract)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO project_revisions VALUES(?,?,?)', (name, revision, canonical(contract).decode()))
            self.db.execute('UPDATE connections SET revision=? WHERE id=?', (revision, name))
        return self.describe(name)

    def _scan(self, name: str) -> tuple[dict, dict[str, bytes]]:
        connection = self.describe(name)
        source = Path(connection['source_root'])
        contract = load_contract(source)
        if digest(contract) != connection['revision']:
            raise ProjectError('Contract changed; project refresh required')
        required = set(contract.required_paths())
        entries, directories, excluded, blockers, content = [], [], [], [], {}
        count = total = 0

        def visit(fd: int, prefix: str):
            nonlocal count, total
            names = []
            with os.scandir(fd) as children:
                for child in children:
                    count += 1
                    if count > self.projects.max_files:
                        raise ProjectError('Source traversal entry bound exceeded')
                    names.append(child.name)
            for name in sorted(names):
                path = f'{prefix}/{name}' if prefix else name
                relative(path)
                mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
                if path == '.git' or any(matches(path, r) for r in contract.source.exclude):
                    excluded.append(path)
                    continue
                selected = path in required or path.startswith('.kestrel/') or any(matches(path, r) for r in contract.source.include)
                if name in ('.git', '.gitmodules'):
                    blockers.append(f'nested repository: {path}')
                elif stat.S_ISDIR(mode):
                    if selected:
                        directories.append(path)
                    child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        visit(child_fd, path)
                    finally:
                        os.close(child_fd)
                elif not selected:
                    excluded.append(path)
                elif not stat.S_ISREG(mode):
                    blockers.append(f'selected link or special file: {path}')
                else:
                    data = _safe_read(source, path, self.projects.max_source_bytes - total)
                    if data.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
                        blockers.append(f'unresolved LFS reference: {path}')
                    total += len(data)
                    content[path] = data
                    entries.append({'path': path, 'digest': hashlib.sha256(data).hexdigest(),
                                    'size': len(data), 'executable': bool(mode & 0o111)})
        fd = _directory_fd(source)
        try:
            visit(fd, '')
        finally:
            os.close(fd)
        blockers.extend(f'missing required file: {p}' for p in sorted(required - content.keys()))
        # Preserve structural parent directories too, for exact snapshot verification.
        for path in [e['path'] for e in entries] + directories[:]:
            directories.extend(str(p) for p in Path(path).parents if str(p) != '.')
        identity = {'format': 'kestrel-selected-source-1', 'project_id': connection['project_id'],
                    'revision': connection['revision'], 'rules': contract.source.model_dump(),
                    'files': entries, 'directories': sorted(set(directories))}
        return {'identity': identity, 'selection_digest': digest(identity), 'files': len(entries),
                'bytes': total, 'excluded': excluded, 'blockers': blockers}, content

    def preview(self, name: str) -> dict:
        return self._scan(name)[0]

    def snapshot(self, name: str, expected: str) -> dict:
        preview, content = self._scan(name)
        if preview['blockers'] or preview['selection_digest'] != expected:
            raise ProjectError('Snapshot blocked or preview digest changed')
        identity = preview['identity']
        target = self.projects.snapshots / expected
        temporary = Path(tempfile.mkdtemp(prefix='.selected-', dir=self.projects.snapshots))
        try:
            for directory in identity['directories']:
                (temporary / directory).mkdir(parents=True, exist_ok=True)
            for entry in identity['files']:
                path = temporary / entry['path']
                path.write_bytes(content[entry['path']])
                path.chmod(0o555 if entry['executable'] else 0o444)
            second = self.preview(name)
            if second['blockers'] or second['selection_digest'] != expected:
                raise ProjectError('Source changed during snapshot')
            if target.exists():
                self.projects._verify_snapshot(target, identity['files'], identity['directories'])
            else:
                os.rename(temporary, target)
            record = {'digest': expected, 'snapshot_path': str(target), **identity}
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO selected_snapshots VALUES(?,?)', (expected, canonical(record).decode()))
            return record
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def get_snapshot(self, identity: str) -> dict:
        row = self.db.execute('SELECT record FROM selected_snapshots WHERE digest=?', (identity,)).fetchone()
        if row is None:
            raise ProjectError('Unknown selected snapshot')
        record = json.loads(row[0])
        original = {k: v for k, v in record.items() if k not in ('digest', 'snapshot_path')}
        if digest(original) != identity or record['snapshot_path'] != str(self.projects.snapshots / identity):
            raise ProjectError('Snapshot record integrity mismatch')
        self.projects._verify_snapshot(Path(record['snapshot_path']), record['files'], record['directories'])
        return record


class Replacement(StrictModel):
    path: str
    original: Digest
    content: str = Field(max_length=1024 * 1024)


def validate_edits(contract: ProjectContract, snapshot: dict, edits: list[Replacement]) -> dict[str, bytes]:
    if len(edits) > 64 or len({e.path for e in edits}) != len(edits):
        raise ProjectError('Duplicate or excessive edits')
    files = {f['path']: f for f in snapshot['files']}
    result = {}
    for edit in edits:
        path = relative(edit.path)
        if path == '.kestrel' or path.startswith('.kestrel/') or path in contract.required_paths() or any(matches(path, r) for r in contract.protected):
            raise ProjectError('Protected candidate path')
        if not any(matches(path, r) for r in contract.editable) or path not in files:
            raise ProjectError('Undeclared candidate path')
        if edit.original != files[path]['digest']:
            raise ProjectError('Stale original hash')
        result[path] = edit.content.encode()
    if sum(map(len, result.values())) > 1024 * 1024:
        raise ProjectError('Edit byte budget exceeded')
    return result
