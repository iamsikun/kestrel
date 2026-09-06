"""Generate the only programs permitted by the development execution profile.

This allowlist is a developer safety restriction, not an adversarial security boundary.
All repository creation happens in explicitly supplied external temporary directories.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from kestrel.contracts import canonical

_PRELUDE = '''import json, os, pathlib, sys
def offline_audit(event, args):
    if event.startswith("socket."):
        raise PermissionError("offline trusted fixture: network disabled")
sys.addaudithook(offline_audit)
request = json.load(sys.stdin)
config = json.loads(pathlib.Path("config.json").read_text())
output = pathlib.Path(request["output_directory"])
output.mkdir(exist_ok=True)
'''
_NUMERICAL = '''xs = request["operation_parameters"]["xs"]
offset = {"baseline": 1, "inferior": 3}[config["method"]]
result = {"kind": "predictions", "ids": list(range(len(xs))),
          "values": [2*x+offset for x in xs], "self_reported_metric": -999.0}
'''
_COUNTEREXAMPLE = '''domain = request["operation_parameters"]["domain"]
strict = config["method"] == "inferior"
witnesses = [n for n in domain if not (n*n > n if strict else n*n >= n)]
result = {"kind": "counterexamples", "strict": strict, "witnesses": witnesses,
          "self_reported_metric": -999.0}
'''
_EPILOGUE = '''temporary = output / "result.json.partial"
temporary.write_text(json.dumps(result, allow_nan=False))
os.replace(temporary, output / "result.json")
print(json.dumps({"protocol_version": "0.1", "attempt_id": request["attempt_id"],
 "status": "success", "produced_artifacts": [{"path": "result.json",
 "media_type": "application/json", "complete": True}],
 "diagnostics": {"network": "python_audit_denied", "fixture": True}}))
'''
WORKERS = {"numerical": _PRELUDE + _NUMERICAL + _EPILOGUE,
           "counterexample": _PRELUDE + _COUNTEREXAMPLE + _EPILOGUE}
CONFIGS = {canonical({"method": method}) for method in ("baseline", "inferior")}


def is_trusted_workspace(path: Path) -> bool:
    """Match bytes of every code/config file, never import the project."""
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_dir():
            return False
        names = {item.name for item in path.iterdir()}
        if names - {"worker.py", "config.json", "output"}:
            return False
        for name in ("worker.py", "config.json"):
            item = path / name
            if item.is_symlink() or not item.is_file() or item.stat().st_nlink != 1:
                return False
        if (path / "output").is_symlink():
            return False
        return (path / "worker.py").read_bytes() in {
            code.encode() for code in WORKERS.values()
        } and (path / "config.json").read_bytes() in CONFIGS
    except OSError:
        return False


def generate(root: Path, framework_root: Path) -> list[Path]:
    root, framework_root = root.resolve(), framework_root.resolve()
    if root.is_relative_to(framework_root) or framework_root.is_relative_to(root):
        raise ValueError("Synthetic repositories must be outside the framework root")
    root.mkdir(parents=True, exist_ok=False)
    manifests = []
    for kind, code in WORKERS.items():
        project = root / kind
        project.mkdir()
        (project / "worker.py").write_text(code)
        (project / "config.json").write_bytes(canonical({"method": "baseline"}))
        env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"}
        # Only newly created trusted synthetic repositories; never enrollment discovery.
        for args in (["init", "-q"], ["add", "worker.py", "config.json"],
                     ["-c", "user.name=Kestrel Fixture", "-c", "user.email=fixture@invalid",
                      "-c", "commit.gpgsign=false", "commit", "-qm", "Synthetic fixture"]):
            subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=project,
                           env=env, check=True, capture_output=True, timeout=10)
        manifest = {
            "protocol_version": "0.1", "project_id": kind,
            "source": {"kind": "directory", "locator": str(project)},
            "adapter": {"identity": hashlib.sha256(code.encode()).hexdigest(),
                        "commands": {"execute": ["python", "-I", "-B", "worker.py"]}},
            "environment": {"kind": "development", "identity": "trusted-fixture-python"},
            "capabilities": {"predicts_without_targets": True, "exact_resume": False},
            "data_policy": {"classification": "public_synthetic", "external_model_access": False},
        }
        target = root / f"{kind}.sidecar.json"
        target.write_text(json.dumps(manifest, indent=2))
        manifests.append(target)
    return manifests
