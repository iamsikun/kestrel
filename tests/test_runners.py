"""Core supervision tests plus opt-in, measured Linux container acceptance.

KESTREL_RUN_ISOLATION=1 KESTREL_DOCKER_IMAGE=sha256:... pytest -m isolation
uses an existing public Python image and never pulls, builds, or changes a daemon.
Default skips are explicitly unavailable deployment evidence, not passing gates.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from kestrel.contracts import canonical
from kestrel.fixtures import WORKERS
from kestrel.runners import (
    DevelopmentDriver,
    DockerDriver,
    DriverError,
    JobSpec,
    _json_write,
    _spec_data,
)


@pytest.fixture
def trusted_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    (workspace / "worker.py").write_text(WORKERS["numerical"])
    (workspace / "config.json").write_bytes(canonical({"method": "baseline"}))
    return workspace


def fixture_spec(workspace: Path, attempt: str = "attempt-1") -> JobSpec:
    return JobSpec(
        attempt,
        workspace,
        (sys.executable, "-I", "-B", "worker.py"),
        {
            "protocol_version": "0.1",
            "attempt_id": attempt,
            "output_directory": "output",
            "operation_parameters": {"xs": [0, 1, 2]},
        },
    )


@pytest.mark.acceptance("A03")
def test_real_fixture_execution_and_restart_reconciliation(tmp_path, trusted_workspace):
    spec = fixture_spec(trusted_workspace)
    driver = DevelopmentDriver(tmp_path / "driver")
    job_id = driver.launch(spec)
    recovered = DevelopmentDriver(tmp_path / "driver")
    assert recovered.launch(spec) == job_id
    result = recovered.wait(job_id, timeout=10)
    assert result.stopped and result.returncode == 0, result
    assert json.loads(result.stdout)["attempt_id"] == spec.attempt_id
    assert json.loads((trusted_workspace / "output/result.json").read_text())["values"] == [1, 3, 5]
    assert recovered.reconcile(spec.attempt_id) == result
    assert len(list((tmp_path / "driver").iterdir())) == 1


@pytest.mark.acceptance("A18")
def test_development_refuses_isolation_and_unknown_code(tmp_path, trusted_workspace):
    driver = DevelopmentDriver(tmp_path / "driver")
    spec = fixture_spec(trusted_workspace)
    with pytest.raises(DriverError, match="security property"):
        driver.launch(replace(spec, required_profile="isolated-local"))
    with pytest.raises(DriverError, match="security property"):
        driver.launch(replace(spec, require_hard_storage=True))
    with pytest.raises(DriverError, match="argument vector"):
        driver.launch(replace(spec, argv=("/bin/sh", "-c", "true")))
    (trusted_workspace / "worker.py").write_text("raise RuntimeError('untrusted candidate')")
    with pytest.raises(DriverError, match="unknown or modified"):
        driver.launch(spec)
    assert list((tmp_path / "driver").iterdir()) == []
    assert driver.probe()["adversarial_isolation"] is False


@pytest.mark.acceptance("A32")
def test_dispatch_crash_does_not_blindly_relaunch(tmp_path, trusted_workspace, monkeypatch):
    driver = DevelopmentDriver(tmp_path / "driver")
    spec = fixture_spec(trusted_workspace)
    directory = tmp_path / "driver" / spec.attempt_id
    directory.mkdir()
    _json_write(directory / "dispatch.json", _spec_data(spec))

    def forbidden(*_args, **_kwargs):
        pytest.fail("uncertain dispatch must not call Popen a second time")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert driver.launch(spec) == spec.attempt_id
    assert driver.reconcile(spec.attempt_id).state == "unknown"
    assert driver.cancel(spec.attempt_id).stopped is False
    with pytest.raises(DriverError, match="already exists"):
        driver.launch(replace(spec, timeout_seconds=2))


@pytest.mark.acceptance("A33")
def test_missing_supervisor_does_not_release_resource(tmp_path, trusted_workspace):
    driver = DevelopmentDriver(tmp_path / "driver")
    spec = fixture_spec(trusted_workspace)
    directory = tmp_path / "driver" / spec.attempt_id
    directory.mkdir()
    _json_write(directory / "dispatch.json", _spec_data(spec))
    _json_write(directory / "supervisor.json", {"pid": 99999999, "identity": "old"})
    status = driver.reconcile(spec.attempt_id)
    assert status.state == "unknown" and not status.stopped
    assert not driver.cancel(spec.attempt_id).stopped


@pytest.mark.acceptance("A15")
@pytest.mark.parametrize(
    "update",
    [
        {"timeout_seconds": 121},
        {"timeout_seconds": float("nan")},
        {"cpu": 3},
        {"memory_mib": 513},
        {"pids": 129},
        {"storage_mib": 257},
    ],
)
def test_fixture_envelope_cannot_expand(trusted_workspace, update):
    with pytest.raises(DriverError):
        replace(fixture_spec(trusted_workspace), **update)


@pytest.mark.acceptance("A18")
def test_docker_requires_pinned_local_image_and_no_hard_storage(tmp_path, trusted_workspace):
    with pytest.raises(DriverError, match="SHA256"):
        DockerDriver("python:latest", tmp_path)
    driver = DockerDriver("sha256:" + "a" * 64, tmp_path, docker="missing-kestrel-docker")
    assert driver.probe()["available"] is False
    with pytest.raises(DriverError, match="unavailable"):
        driver.launch(fixture_spec(trusted_workspace))
    with pytest.raises(DriverError, match="hard writable"):
        driver.launch(replace(fixture_spec(trusted_workspace), require_hard_storage=True))


@pytest.fixture
def docker_driver(tmp_path, request):
    if os.environ.get("KESTREL_RUN_ISOLATION") != "1":
        pytest.skip("A12-A15/A17/A20 isolation gate requires explicit measured Docker invocation")
    image = os.environ.get("KESTREL_DOCKER_IMAGE")
    if not image:
        pytest.fail("explicit isolation invocation requires KESTREL_DOCKER_IMAGE=sha256:...")
    driver = DockerDriver(image, tmp_path)
    if not driver.probe()["available"]:
        pytest.fail("required Linux runtime/image/resources unavailable; isolation gate NOT passed")
    jobs = []
    original = driver.launch

    def launch(spec):
        identity = DockerDriver._name(spec.attempt_id)
        jobs.append(identity)
        return original(spec)

    driver.launch = launch
    yield driver
    for identity in jobs:
        observed = driver.inspect(identity)
        runtime = driver._lookup(identity)
        request.node.user_properties.append(
            (
                "kestrel_job_evidence",
                json.dumps(
                    {
                        "job_id": identity,
                        "state": observed.state,
                        "stopped": observed.stopped,
                        "returncode": observed.returncode,
                        "stdout": observed.stdout.decode(errors="replace"),
                        "stderr": observed.stderr.decode(errors="replace"),
                        "detail": observed.detail,
                        "image": driver.image,
                        "runtime_state": runtime["State"] if runtime else None,
                    },
                    sort_keys=True,
                ),
            )
        )
        status = driver.cancel(identity)
        if not status.stopped:
            pytest.fail(f"container cleanup could not confirm complete stop: {identity}")
        driver.remove(identity)


@pytest.mark.acceptance("A18")
def test_trusted_fixture_output_path_cannot_redirect_host_writes(tmp_path, trusted_workspace):
    driver = DevelopmentDriver(tmp_path / "driver")
    spec = fixture_spec(trusted_workspace)
    with pytest.raises(DriverError, match="fixed output"):
        driver.launch(replace(spec, request={**spec.request, "output_directory": str(tmp_path)}))
    output = trusted_workspace / "output"
    output.mkdir()
    sentinel = tmp_path / "public-forbidden-sentinel"
    sentinel.write_text("unchanged")
    (output / "result.json.partial").symlink_to(sentinel)
    with pytest.raises(DriverError, match="unsafe existing outputs"):
        driver.launch(spec)
    assert sentinel.read_text() == "unchanged"


def docker_spec(tmp_path, code, attempt="isolated-job", **kwargs):
    attempt += "-" + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
    workspace = tmp_path / attempt
    workspace.mkdir(mode=0o777)
    workspace.chmod(0o777)
    (workspace / "worker.py").write_text(code)
    return JobSpec(
        attempt,
        workspace,
        ("/usr/local/bin/python", "-I", "-B", "worker.py"),
        {},
        required_profile="isolated-local",
        **kwargs,
    )


@pytest.mark.isolation
@pytest.mark.acceptance("A12")
@pytest.mark.acceptance("A13")
@pytest.mark.acceptance("A20")
def test_measured_mounts_targets_controller_peer_and_secrets_denied(
    docker_driver, tmp_path, monkeypatch
):
    protected = tmp_path / "protected"
    protected.mkdir()
    sentinels = []
    for name in ("controller.sqlite", "policy.json", "peer-candidate.py", "sealed-targets.json"):
        path = protected / name
        path.write_text("public synthetic forbidden sentinel")
        sentinels.append(str(path))
    monkeypatch.setenv("KESTREL_SYNTHETIC_SECRET", "never-inherit-this-sentinel")
    forbidden = sentinels + [
        "/root/.ssh",
        "/var/run/docker.sock",
        "/host_mnt",
        "/mnt/c",
        "/run/host-services/ssh-auth.sock",
    ]
    code = f"""
import json,os,pathlib
for text in {forbidden!r}:
    path=pathlib.Path(text)
    try:
        path.read_bytes()
    except (OSError,PermissionError):
        pass
    else:
        raise AssertionError('forbidden read succeeded '+text)
    try:
        path.write_text('mutation')
    except (OSError,PermissionError):
        pass
    else:
        raise AssertionError('forbidden write succeeded '+text)
assert pathlib.Path('/workspace/worker.py').is_file()
assert os.getuid()==65534
assert 'KESTREL_SYNTHETIC_SECRET' not in os.environ
assert b'never-inherit-this-sentinel' not in pathlib.Path('/proc/self/environ').read_bytes()
print(json.dumps({{'forbidden_attempts':len({forbidden!r}), 'uid':os.getuid()}}))
"""
    spec = docker_spec(tmp_path, code)
    job = docker_driver.launch(spec)
    status = docker_driver.wait(job, timeout=20)
    assert status.stopped and status.returncode == 0, status
    assert json.loads(status.stdout)["forbidden_attempts"] == len(forbidden)
    data = docker_driver._lookup(job)
    binds = [x for x in data["Mounts"] if x["Type"] == "bind"]
    assert [(x["Source"], x["Destination"]) for x in binds] == [(str(spec.workspace), "/workspace")]
    assert data["HostConfig"]["ReadonlyRootfs"]
    assert data["HostConfig"]["CapDrop"] == ["ALL"]
    assert data["HostConfig"]["NetworkMode"] == "none"
    assert all(
        Path(item).read_text() == "public synthetic forbidden sentinel" for item in sentinels
    )


@pytest.mark.isolation
@pytest.mark.enable_socket
@pytest.mark.acceptance("A14")
def test_measured_network_denied_including_child(docker_driver, tmp_path):
    # Real local endpoint; a positive host-side connection establishes it is live.
    with socket.socket() as listener:
        listener.bind(("0.0.0.0", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            connection, _ = listener.accept()
            connection.close()
        code = f"""
import pathlib,socket,subprocess,sys
interfaces=sorted(p.name for p in pathlib.Path('/sys/class/net').iterdir())
# Newer Linux kernels expose dormant tunnel devices even with network=none.
# Require no active non-loopback device and no IPv4 route, then try real egress.
for interface in interfaces:
    flags=pathlib.Path('/sys/class/net')/interface/'flags'
    if interface!='lo' and flags.is_file():
        assert not int(flags.read_text(),16)&1,interface
routes=pathlib.Path('/proc/net/route').read_text().splitlines()
assert len(routes)==1,repr(routes)
for address in ('127.0.0.1','192.0.2.1'):
    try:
        socket.create_connection((address,{port}),timeout=.2)
    except OSError:
        pass
    else:
        raise AssertionError('network connection escaped')
child="import socket; socket.create_connection(('127.0.0.1',{port}),timeout=.2)"
assert subprocess.run([sys.executable,'-I','-c',child],capture_output=True).returncode!=0
print('parent and child denied')
"""
        status = docker_driver.wait(docker_driver.launch(docker_spec(tmp_path, code)), timeout=20)
        assert status.stopped and status.returncode == 0, status.stderr.decode()
        listener.settimeout(0.1)
        with pytest.raises(TimeoutError):
            listener.accept()


@pytest.mark.isolation
@pytest.mark.acceptance("A15")
def test_measured_cpu_quota_throttles_bounded_busy_loop(docker_driver, tmp_path):
    code = """
import json,pathlib,resource,time
root=pathlib.Path('/sys/fs/cgroup')
quota,period=map(int,(root/'cpu.max').read_text().split())
assert quota/period==.25
before=int(dict(line.split() for line in (root/'cpu.stat').read_text().splitlines())['nr_throttled'])
start=time.monotonic()
while time.monotonic()-start<1.5:
    pass
used=resource.getrusage(resource.RUSAGE_SELF).ru_utime
now=int(dict(line.split() for line in (root/'cpu.stat').read_text().splitlines())['nr_throttled'])
assert now>before
assert used<.8
print(json.dumps({'cpu_seconds':used,'throttled_periods':now-before,'quota':quota,'period':period}))
"""
    spec = docker_spec(tmp_path, code, cpu=0.25, timeout_seconds=10)
    result = docker_driver.wait(docker_driver.launch(spec), timeout=20)
    assert result.stopped and result.returncode == 0, result
    assert json.loads(result.stdout)["throttled_periods"] > 0


@pytest.mark.isolation
@pytest.mark.acceptance("A15")
def test_measured_memory_cgroup_stops_excess_allocation(docker_driver, tmp_path):
    code = "import time; allocations=[bytearray(1024*1024) for _ in range(160)]; time.sleep(1)"
    spec = docker_spec(tmp_path, code, memory_mib=64, timeout_seconds=10)
    job = docker_driver.launch(spec)
    result = docker_driver.wait(job, timeout=20)
    assert result.stopped and result.returncode != 0, result
    assert docker_driver._lookup(job)["State"]["OOMKilled"], result


@pytest.mark.isolation
@pytest.mark.acceptance("A15")
def test_measured_pid_cgroup_refuses_excess_children(docker_driver, tmp_path):
    code = """
import json,pathlib,subprocess,sys
assert pathlib.Path('/sys/fs/cgroup/pids.max').read_text().strip()=='16'
children=[]
blocked=False
try:
    for _ in range(40):
        try:
            children.append(subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']))
        except OSError:
            blocked=True
            break
finally:
    for child in children:
        child.kill()
        child.wait()
assert blocked and 1<=len(children)<16
print(json.dumps({'children':len(children),'refused':blocked}))
"""
    spec = docker_spec(tmp_path, code, pids=16, timeout_seconds=10)
    result = docker_driver.wait(docker_driver.launch(spec), timeout=20)
    assert result.stopped and result.returncode == 0, result
    assert json.loads(result.stdout)["refused"]


@pytest.mark.isolation
@pytest.mark.acceptance("A15")
def test_measured_tmpfs_cap_and_readonly_root(docker_driver, tmp_path):
    code = """
import errno,pathlib
try:
    pathlib.Path('/write-root-sentinel').write_text('forbidden')
except OSError as error:
    assert error.errno in (errno.EROFS,errno.EACCES)
else:
    raise AssertionError('root is writable')
try:
    pathlib.Path('/tmp/overflow').write_bytes(b'x'*(10*1024*1024))
except OSError as error:
    assert error.errno==errno.ENOSPC
else:
    raise AssertionError('tmpfs cap missing')
print('root denied; tmpfs ENOSPC')
"""
    result = docker_driver.wait(docker_driver.launch(docker_spec(tmp_path, code)), timeout=20)
    assert result.stopped and result.returncode == 0, result


@pytest.mark.isolation
@pytest.mark.acceptance("A15")
def test_measured_workspace_storage_is_advisory_and_hard_request_refused(docker_driver, tmp_path):
    code = "import pathlib,time; pathlib.Path('overflow').write_bytes(b'x'*(2*1024*1024)); time.sleep(15)"
    spec = docker_spec(tmp_path, code, storage_mib=1, timeout_seconds=20)
    with pytest.raises(DriverError, match="hard writable"):
        docker_driver.launch(replace(spec, require_hard_storage=True))
    result = docker_driver.wait(docker_driver.launch(spec), timeout=20)
    assert result.stopped and result.returncode != 0, result
    assert "advisory storage watchdog" in result.detail
    assert docker_driver.probe()["storage_hard"] is False


@pytest.mark.isolation
@pytest.mark.acceptance("A15")
@pytest.mark.acceptance("A32")
def test_measured_deadline_survives_controller_restart(docker_driver, tmp_path):
    spec = docker_spec(tmp_path, "import time; time.sleep(30)", timeout_seconds=0.3)
    job = docker_driver.launch(spec)
    restarted = DockerDriver(docker_driver.image, tmp_path)
    assert restarted.launch(spec) == job
    result = restarted.wait(job, timeout=20)
    docker_driver._guards[job].wait(timeout=5)
    result = restarted.inspect(job)
    assert result.stopped and result.returncode in (124, 137), result
    assert b"wall deadline" in result.stderr or result.detail == "host wall deadline exceeded"
    assert restarted.reconcile(spec.attempt_id).stopped


@pytest.mark.isolation
@pytest.mark.acceptance("A17")
def test_measured_cancellation_stops_container_and_entire_child_tree(docker_driver, tmp_path):
    code = """
import subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'])
print(child.pid,flush=True)
time.sleep(60)
"""
    spec = docker_spec(tmp_path, code)
    job = docker_driver.launch(spec)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if docker_driver._call(["container", "logs", job]).stdout.strip():
            break
        time.sleep(0.05)
    else:
        pytest.fail("candidate did not spawn its child")
    result = docker_driver.cancel(job)
    assert result.stopped and result.returncode != 0, result
    assert docker_driver._lookup(job)["State"]["Running"] is False
    top = docker_driver._call(["container", "top", job], check=False)
    assert top.returncode != 0 and b"not running" in top.stderr.lower()


@pytest.mark.isolation
@pytest.mark.acceptance("A15")
def test_host_deadline_cannot_be_stopped_by_candidate(docker_driver, tmp_path):
    code = "import os,signal,time; os.kill(os.getppid(),signal.SIGSTOP); time.sleep(30)"
    spec = docker_spec(tmp_path, code, timeout_seconds=1.5)
    job = docker_driver.launch(spec)
    restarted = DockerDriver(docker_driver.image, tmp_path)
    result = restarted.wait(job, timeout=15)
    assert result.stopped and result.returncode != 0, result
    assert docker_driver._lookup(job)["State"]["Running"] is False


@pytest.mark.isolation
@pytest.mark.acceptance("A17")
def test_cancellation_fences_pending_detached_start(docker_driver, tmp_path):
    spec = docker_spec(tmp_path, "import time; time.sleep(30)")
    job = docker_driver.launch(spec)
    result = docker_driver.cancel(job)
    assert result.stopped, result
    time.sleep(0.3)
    assert not docker_driver._lookup(job)["State"]["Running"]


@pytest.mark.acceptance("A15")
def test_storage_watchdog_never_follows_symlinks_or_unbounded_metadata(tmp_path):
    from kestrel.runners import _directory_size

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "forbidden-synthetic"
    external.mkdir()
    (external / "large").write_bytes(b"x" * 100)
    (workspace / "link").symlink_to(external, target_is_directory=True)
    assert _directory_size(workspace, 10) == 0
    for index in range(66):
        (workspace / str(index)).mkdir()
    assert _directory_size(workspace, 10) > 10
