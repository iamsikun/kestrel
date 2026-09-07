"""CLI routing to the same services exposed by Client."""
from pathlib import Path

from .contracts import parse_json
from .experiments import Experiments
from .integration import Connections


def add_parsers(project, commands):
    init = project.add_parser('init')
    init.add_argument('path', type=Path)
    init.add_argument('--json', action='store_true')
    add = project.add_parser('add')
    add.add_argument('path', type=Path)
    add.add_argument('--name')
    add.add_argument('--json', action='store_true')
    project.add_parser('list').add_argument('--json', action='store_true')
    for action in ('inspect', 'refresh', 'snapshot'):
        parser = project.add_parser(action)
        parser.add_argument('project')
        parser.add_argument('--json', action='store_true')
        if action == 'snapshot':
            mode = parser.add_mutually_exclusive_group(required=True)
            mode.add_argument('--preview', action='store_true')
            mode.add_argument('--expect')
    experiments = commands.add_parser('experiment').add_subparsers(dest='action', required=True)
    propose = experiments.add_parser('propose')
    propose.add_argument('--snapshot', required=True)
    propose.add_argument('--operation', required=True)
    propose.add_argument('--parameters', default='{}', help='JSON parameter object')
    propose.add_argument('--edits', type=Path, help='JSON list of bounded replacements')
    propose.add_argument('--limits', default='{}', help='JSON limits object')
    propose.add_argument('--json', action='store_true')
    for action in ('inspect', 'approve', 'run', 'status', 'cancel', 'artifacts', 'export'):
        parser = experiments.add_parser(action)
        parser.add_argument('experiment')
        parser.add_argument('--json', action='store_true')
        if action == 'approve':
            parser.add_argument('--digest', required=True)
            parser.add_argument('--operator-token-file', type=Path, required=True)
        if action == 'run':
            parser.add_argument('--approval', required=True)
        if action == 'export':
            parser.add_argument('--output', type=Path, required=True)


def dispatch(lab, args):
    if args.command == 'project':
        service = Connections(lab.projects)
        if args.action == 'add':
            return service.add(args.path, args.name)
        if args.action == 'list':
            return service.list()
        if args.action == 'inspect':
            return service.describe(args.project)
        if args.action == 'refresh':
            return service.refresh(args.project)
        if args.preview:
            return service.preview(args.project)
        return service.snapshot(args.project, args.expect)
    service = Experiments(lab)
    if args.action == 'propose':
        from .artifacts import _safe_read
        edits = parse_json(_safe_read(args.edits.parent, args.edits.name, 2 * 1024**2), max_bytes=2 * 1024**2) if args.edits else []
        return service.propose(args.snapshot, args.operation, parse_json(args.parameters), edits, parse_json(args.limits))
    if args.action == 'approve':
        return {'approval_id': service.approve(args.experiment, args.digest, args.operator_token_file.read_text().strip())}
    if args.action == 'run':
        return service.run(args.experiment, args.approval)
    if args.action == 'cancel':
        return service.cancel(args.experiment)
    if args.action == 'artifacts':
        return service.artifacts(args.experiment)
    if args.action == 'export':
        return {'bundle': str(service.export(args.experiment, args.output))}
    return service.inspect(args.experiment)


def readable(result):
    """Readable terminal output; JSON mode retains the complete stable data shape."""
    import json
    if isinstance(result, list):
        return '\n\n'.join(readable(item) for item in result) or 'No records.'
    return '\n'.join(f'{key.replace("_", " ")}: ' + (json.dumps(value, indent=2, sort_keys=True)
                     if isinstance(value, (dict, list)) else str(value)) for key, value in result.items())
