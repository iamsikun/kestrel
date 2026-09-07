"""Generate two ordinary synthetic external projects for the onboarding tutorial."""
import argparse
from pathlib import Path

import yaml

from . import adapter
from .integration import initialize


def generate(root: Path, image: str) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    result = []
    for name, operation, expression in [('arithmetic', 'calculate', 'args.value * FACTOR'),
                                         ('strings', 'repeat', '"sample" * args.value')]:
        source = root / name
        source.mkdir()
        initialize(source)
        (source / 'GUIDE.md').write_text(f'# {name}\n\nRun {operation} with integer value. Outputs are self-reported.\n')
        (source / 'program.py').write_text('import argparse, json\nfrom pathlib import Path\n'
            'FACTOR = 2\np = argparse.ArgumentParser()\np.add_argument("--value", type=int, required=True)\n'
            f'args = p.parse_args()\nPath("output/result.json").write_text(json.dumps({{"result": {expression}}}))\n')
        (source / '.kestrel/helper.py').write_text(Path(adapter.__file__).read_text())
        (source / '.kestrel/adapter.py').write_text('from helper import command\n'
            'raise SystemExit(command(["python3", "program.py"], [("result.json", "application/json")]))\n')
        contract = {'version': '1', 'purpose': f'Synthetic {name} example',
            'references': {k: ['GUIDE.md'] for k in ('architecture', 'experiments', 'configuration')},
            'adapter': '.kestrel/adapter.py', 'operations': {operation: {
                'description': operation, 'argv': ['python3', '.kestrel/adapter.py'], 'inputs': 'integer value',
                'outputs': 'result.json', 'artifact_types': ['application/json'], 'parameters': {
                    'value': {'type': 'integer', 'description': 'Input count', 'required': True,
                              'default': 3, 'minimum': 1, 'maximum': 10}}}},
            'editable': ['program.py'], 'protected': ['GUIDE.md'],
            'source': {'include': ['program.py'], 'exclude': ['data/**']},
            'classification': 'public_synthetic', 'image': image}
        (source / '.kestrel/project.yaml').write_text(yaml.safe_dump(contract, sort_keys=False))
        result.append(source)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    for path in generate(args.output, args.image):
        print(path)
