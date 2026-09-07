"""Install and run the built distribution outside the source checkout.

Setup: ``uv build`` and a populated uv dependency cache, or an external wheelhouse.
Run with KESTREL_WHEEL=/absolute/path/to/kestrel_research_runtime-*.whl.
KESTREL_WHEELHOUSE optionally supplies all dependency wheels for an empty-cache
installation. This test never downloads packages or builds dependency sources.
Missing setup is an explicitly skipped, unsatisfied package-install gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

IMPORT_CHECK = """
import importlib, importlib.abc, importlib.metadata, json, pathlib, sys
forbidden = {'torch', 'tensorflow', 'jax', 'docker', 'openai', 'anthropic', 'codex'}
attempted = []
class DenyHeavyDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in forbidden:
            attempted.append(fullname)
            raise ImportError('Core import attempted an unavailable heavyweight/provider dependency')
sys.meta_path.insert(0, DenyHeavyDependencies())
modules = ['kestrel', 'kestrel.application', 'kestrel.controller', 'kestrel.contracts',
           'kestrel.projects', 'kestrel.artifacts', 'kestrel.runners',
           'kestrel.evaluation', 'kestrel.agents', 'kestrel.agent_execution',
           'kestrel.provider_records', 'kestrel.conformance', 'kestrel.cli',
           'kestrel.client', 'kestrel.integration', 'kestrel.experiments']
locations = {name: str(pathlib.Path(importlib.import_module(name).__file__).resolve())
             for name in modules}
environment = pathlib.Path(sys.prefix).resolve()
assert all(pathlib.Path(location).is_relative_to(environment) for location in locations.values())
assert not attempted, attempted
installed = {distribution.metadata['Name']: distribution.version
             for distribution in importlib.metadata.distributions()}
assert not {name.lower().replace('-', '_') for name in installed} & forbidden, installed
assert sys.prefix != sys.base_prefix
print(json.dumps({'python': sys.version, 'prefix': sys.prefix, 'modules': locations,
                  'installed': installed, 'blocked_import_attempts': attempted}, sort_keys=True))
"""


OFFLINE_CONSOLE_LAUNCHER = """
# This hook constrains the trusted Python CLI, not arbitrary adversarial processes.
import runpy, sys
def offline_audit(event, args):
    if event.startswith('socket.'):
        raise PermissionError('package-install acceptance: controller network is disabled')
sys.addaudithook(offline_audit)
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""


@pytest.mark.install
@pytest.mark.acceptance("A01")
@pytest.mark.acceptance("A41")
def test_built_wheel_installs_cleanly_and_runs_actual_external_offline_demo(tmp_path, request):
    wheel_value = os.environ.get("KESTREL_WHEEL")
    if not wheel_value:
        pytest.skip(
            "Package-install gate unavailable: build the wheel and set absolute KESTREL_WHEEL"
        )
    wheel = Path(wheel_value)
    if not wheel.is_absolute() or not wheel.is_file() or wheel.suffix != ".whl":
        pytest.fail(
            "KESTREL_WHEEL must name an existing absolute built wheel, not a source checkout"
        )
    wheel = wheel.resolve()
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip(
            "Package-install gate unavailable: uv is required for offline wheel installation"
        )
    checkout = Path(__file__).resolve().parents[1]
    temporary = tmp_path.resolve()
    assert not temporary.is_relative_to(checkout)
    environment_root = temporary / "clean-environment"
    outside_cwd = temporary / "external-cwd"
    outside_cwd.mkdir()
    evidence = {
        "schema_version": "0.1",
        "platform": platform.platform(),
        "wheel": str(wheel),
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "checkout": str(checkout),
        "external_cwd": str(outside_cwd),
        "commands": [],
        "network": "uv offline; Python audit denial in trusted CLI and fixture workers",
        "adversarial_network_isolation": False,
    }
    evidence_value = os.environ.get("KESTREL_INSTALL_EVIDENCE")
    evidence_path = (
        Path(evidence_value).resolve() if evidence_value else temporary / "install-evidence.json"
    )
    if evidence_path.is_relative_to(checkout):
        pytest.fail(
            "Installation verification artifacts must be external to the framework checkout"
        )
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    request.node.user_properties.append(("install_evidence", str(evidence_path)))
    request.node.user_properties.append(("wheel_sha256", evidence["wheel_sha256"]))

    # An allowlist avoids inherited Python hooks, project config, API keys,
    # provider settings, proxy credentials, or active virtual environments.
    # HOME is not reassigned: uv may read its already prepared standard cache.
    environment = {
        "PATH": os.pathsep.join([str(Path(uv).parent), "/usr/bin", "/bin"]),
        "LANG": "C",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_OFFLINE": "1",
        "UV_NO_CONFIG": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if "UV_CACHE_DIR" in os.environ:
        environment["UV_CACHE_DIR"] = os.environ["UV_CACHE_DIR"]

    def run_command(
        arguments: list[str], name: str, timeout: float = 60
    ) -> subprocess.CompletedProcess:
        started = time.monotonic()
        result = subprocess.run(
            arguments,
            cwd=outside_cwd,
            env=environment,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        stdout_path = temporary / f"{name}.stdout"
        stderr_path = temporary / f"{name}.stderr"
        stdout_path.write_text(result.stdout)
        stderr_path.write_text(result.stderr)
        evidence["commands"].append(
            {
                "argv": arguments,
                "cwd": str(outside_cwd),
                "exit_status": result.returncode,
                "elapsed_seconds": time.monotonic() - started,
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
                "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
                "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
            }
        )
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True))
        assert result.returncode == 0, (
            f"{name} failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
        )
        return result

    # A real empty venv, without pip or access to the development environment.
    run_command(
        [sys.executable, "-I", "-m", "venv", "--without-pip", str(environment_root)], "create-venv"
    )
    installed_python = environment_root / "bin" / "python"
    installed_console = environment_root / "bin" / "kestrel"
    empty = run_command(
        [
            str(installed_python),
            "-I",
            "-c",
            "import importlib.metadata,json; print(json.dumps([d.metadata['Name'] for d in importlib.metadata.distributions()]))",
        ],
        "empty-venv",
    )
    assert json.loads(empty.stdout) == []
    install = [
        uv,
        "pip",
        "install",
        "--offline",
        "--no-config",
        "--no-build",
        "--no-python-downloads",
        "--link-mode",
        "copy",
        "--python",
        str(installed_python),
    ]
    wheelhouse_value = os.environ.get("KESTREL_WHEELHOUSE")
    if wheelhouse_value:
        wheelhouse = Path(wheelhouse_value)
        if not wheelhouse.is_absolute() or not wheelhouse.is_dir():
            pytest.fail("KESTREL_WHEELHOUSE must name an existing absolute external directory")
        wheelhouse = wheelhouse.resolve()
        if wheelhouse.is_relative_to(checkout):
            pytest.fail("Dependency wheelhouse must be external to the source checkout")
        install.extend(
            [
                "--no-index",
                "--find-links",
                str(wheelhouse),
                "--cache-dir",
                str(temporary / "empty-uv-cache"),
            ]
        )
        evidence["dependency_wheels"] = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(wheelhouse.glob("*.whl"))
        }
    install.append(str(wheel))
    run_command(install, "install-wheel")
    assert installed_console.is_file()
    imports = run_command(
        [str(installed_python), "-I", "-c", IMPORT_CHECK], "import-installed-core"
    )
    evidence["installed_environment"] = json.loads(imports.stdout)
    assert all(
        not Path(path).is_relative_to(checkout)
        for path in evidence["installed_environment"]["modules"].values()
    )
    launcher = temporary / "offline-console-launcher.py"
    launcher.write_text(OFFLINE_CONSOLE_LAUNCHER)
    demo_root = temporary / "generated-offline-lab"
    demonstration = run_command(
        [
            str(installed_python),
            "-I",
            str(launcher),
            str(installed_console),
            "demo",
            "--offline",
            "--output",
            str(demo_root),
        ],
        "installed-offline-demo",
    )
    summary = json.loads(demonstration.stdout)
    assert summary["provider_calls"] == 0
    assert summary["profile"] == "development"
    assert len(summary["reports"]) == 2
    assert {report["evidence"][0]["analysis"]["treatment"] for report in summary["reports"]} == {
        2,
        4,
    }
    for report in summary["reports"]:
        assert report["state"] == "COMPLETE"
        assert report["execution"] == "succeeded"
        assert report["validity"] == "valid"
        assert report["finding"] == "not_supported"
        assert report["budget_reserved"]["provider_calls"] == 0
        assert report["adversarial_isolation"] is False
        assert len(report["attempts"]) == 3
        assert sum(a["backend"] == "development" for a in report["attempts"]) == 2
        assert sum(a["backend"] == "offline-agent" for a in report["attempts"]) == 1
    projects = [path for path in (demo_root / "fixtures").iterdir() if path.is_dir()]
    assert len(projects) == 2
    assert all(not path.resolve().is_relative_to(checkout) for path in projects)
    packet = Path(summary["evidence_packet"])
    assert packet.is_file()
    evidence["demo_sha256"] = hashlib.sha256((demo_root / "demo.json").read_bytes()).hexdigest()
    evidence["evidence_packet_sha256"] = hashlib.sha256(packet.read_bytes()).hexdigest()
    # Installed onboarding operates only on newly generated external synthetic data.
    integration_image = os.environ.get("KESTREL_INTEGRATION_IMAGE")
    if integration_image:
        docker = shutil.which("docker")
        assert docker, "Installed Docker gate requires an existing Docker CLI"
        environment["PATH"] += os.pathsep + str(Path(docker).parent)
        if "HOME" in os.environ:
            environment["HOME"] = os.environ["HOME"]  # Docker controller context; never mounted into workers.
    integration_sources = temporary / "integration-sources"
    run_command([str(installed_python), "-I", "-m", "kestrel.integration_examples",
                 str(integration_sources), "--image", integration_image or "python@sha256:" + "1" * 64], "generate-integration")
    integration_lab = temporary / "integration-lab"
    def console(arguments, name):
        return run_command([str(installed_python), "-I", str(launcher), str(installed_console),
                            *arguments], name)
    console(["lab", "init", str(integration_lab)], "integration-lab-init")
    prefix = ["--lab", str(integration_lab)]
    for project in ("arithmetic", "strings"):
        console([*prefix, "project", "add", str(integration_sources / project), "--json"],
                f"integration-add-{project}")
        preview = json.loads(console([*prefix, "project", "snapshot", project, "--preview", "--json"],
                                     f"integration-preview-{project}").stdout)
        assert not preview["blockers"]
        snapshot = json.loads(console([*prefix, "project", "snapshot", project, "--expect",
                                       preview["selection_digest"], "--json"], f"integration-snapshot-{project}").stdout)
        operation = "calculate" if project == "arithmetic" else "repeat"
        program = integration_sources / project / "program.py"
        original = program.read_bytes()
        edits_path = temporary / f"{project}-edits.json"
        edits_path.write_text(json.dumps([{"path": "program.py", "original": hashlib.sha256(original).hexdigest(),
                                          "content": original.decode().replace("FACTOR = 2", "FACTOR = 4")}]))
        proposal = json.loads(console([*prefix, "experiment", "propose", "--edits", str(edits_path), "--snapshot", snapshot["digest"],
                                       "--operation", operation, "--parameters", '{"value":5}', "--json"],
                                      f"integration-propose-{project}").stdout)
        assert proposal["budget_charged"]["attempts"] == 0
        if integration_image:
            approval = json.loads(console([*prefix, "experiment", "approve", proposal["experiment_id"],
                "--digest", proposal["digest"], "--operator-token-file", str(integration_lab / "operator.token"),
                "--json"], f"integration-approve-{project}").stdout)
            result = json.loads(console([*prefix, "experiment", "run", proposal["experiment_id"],
                "--approval", approval["approval_id"], "--json"], f"integration-run-{project}").stdout)
            assert result["outcome"]["execution_status"] == "succeeded"
            assert result["scientific_outcome"] == "not_evaluated"
            assert program.read_bytes() == original
            console([*prefix, "experiment", "export", proposal["experiment_id"], "--output",
                     str(temporary / f"{project}-experiment.zip"), "--json"], f"integration-export-{project}")
            # Cleanup only this installed test's confirmed stopped containers.
            run_command([str(installed_python), "-I", "-c",
                "from kestrel.client import Client; import sys; "
                "c=Client(sys.argv[1]); s=c.experiments; i=sys.argv[2]; d=s._driver(s._contract(i)); "
                "[d.remove(d._name(a['backend_label'])) for a in c.lab.controller.attempts(i)]; c.lab.close()",
                str(integration_lab), proposal["experiment_id"]], f"integration-cleanup-{project}")
        else:
            console([*prefix, "experiment", "cancel", proposal["experiment_id"], "--json"],
                    f"integration-cancel-{project}")
    listed = json.loads(console([*prefix, "project", "list", "--json"], "integration-list").stdout)
    assert len(listed) == 2
    evidence["result"] = "passed"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True))
