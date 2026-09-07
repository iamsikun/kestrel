"""Optional worker-only helper for plain commands; never called by the controller.

Copy this dependency-free module into .kestrel/helper.py when the worker image
has no Kestrel installation. Commands use argv, parameters append as --key value.
"""
import json
import subprocess
import sys
from pathlib import Path


def command(argv, artifacts):
    request = json.loads(sys.stdin.buffer.read(65537))
    parameters = request['operation_parameters']
    arguments = list(argv)
    for key, value in parameters.items():
        arguments.extend(['--' + key, str(value)])
    Path('output').mkdir(exist_ok=True)
    process = subprocess.run(arguments, capture_output=True)
    sys.stderr.buffer.write(process.stdout + process.stderr)
    print(json.dumps({'protocol_version': '0.1', 'attempt_id': request['attempt_id'],
        'status': 'success' if process.returncode == 0 else 'failure',
        'produced_artifacts': [{'path': path, 'media_type': media, 'complete': True} for path, media in artifacts],
        'diagnostics': {'returncode': process.returncode}}))
    return process.returncode
