"""Bounded fixture processes and a fail-closed Linux Docker job driver.

The development driver is explicitly NOT an adversarial security boundary. Its
allowlist admits only framework-generated fixture programs. Docker is optional;
no Docker SDK, daemon connection, or project import happens at module import.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import platform
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

MAX_OUTPUT = 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class DriverError(RuntimeError):
    """A requested launch or claimed guarantee is not available."""


@dataclass(frozen=True)
class JobSpec:
    attempt_id: str
    workspace: Path
    argv: tuple[str, ...]
    request: dict[str, Any]
    timeout_seconds: float = 120
    cpu: float = 1
    memory_mib: int = 512
    pids: int = 64
    storage_mib: int = 64
    required_profile: str = "development"
    require_hard_storage: bool = False

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.attempt_id):
            raise DriverError("invalid attempt identity")
        if (
            not isinstance(self.argv, tuple)
            or not self.argv
            or any(not isinstance(x, str) or not x or "\0" in x for x in self.argv)
        ):
            raise DriverError("command must be a nonempty argument vector")
        if not isinstance(self.workspace, Path) or not self.workspace.is_dir():
            raise DriverError("workspace must be an existing directory")
        if self.workspace.is_symlink():
            raise DriverError("workspace symlinks are not permitted")
        if self.required_profile not in {"development", "isolated-local"}:
            raise DriverError("unsupported execution profile")
        for value, maximum, name in (
            (self.timeout_seconds, 120, "wall time"),
            (self.cpu, 2, "CPU"),
            (self.memory_mib, 512, "memory"),
            (self.pids, 128, "PIDs"),
            (self.storage_mib, 256, "storage"),
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or not 0 < value <= maximum
            ):
                raise DriverError(f"invalid or excessive fixture {name} envelope")
        if any(type(value) is not int for value in (self.memory_mib, self.pids, self.storage_mib)):
            raise DriverError("memory, PID and storage caps must be integers")
        if self.memory_mib < 16:
            raise DriverError("fixture memory must be at least 16 MiB")
        encoded = json.dumps(self.request, allow_nan=False).encode()
        if len(encoded) > 65536:
            raise DriverError("request exceeds 64 KiB")


@dataclass(frozen=True)
class JobStatus:
    job_id: str
    state: str
    returncode: int | None = None
    stdout: bytes = b""
    stderr: bytes = b""
    stopped: bool = False
    detail: str = ""


class JobDriver(Protocol):
    def launch(self, spec: JobSpec) -> str: ...
    def inspect(self, job_id: str) -> JobStatus: ...
    def cancel(self, job_id: str) -> JobStatus: ...
    def reconcile(self, attempt_id: str) -> JobStatus: ...
    def probe(self) -> dict[str, Any]: ...


def _json_write(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, sort_keys=True, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _spec_data(spec: JobSpec) -> dict[str, Any]:
    data = asdict(spec)
    data["workspace"] = str(spec.workspace.resolve())
    return data


def _spec_digest(spec: JobSpec) -> str:
    return hashlib.sha256(json.dumps(_spec_data(spec), sort_keys=True).encode()).hexdigest()


@contextmanager
def _guard_lock(directory: Path):
    import fcntl

    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / "dispatch.lock").open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _identity(pid: int) -> str | None:
    """Kernel process start identity, used only for trusted fixture supervision."""
    try:
        if platform.system() == "Linux":
            value = Path(f"/proc/{pid}/stat").read_text()
            return value[value.rindex(")") + 2 :].split()[19]
        result = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            timeout=2,
            check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
        return result.stdout.decode().strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _group_live(pgid: int) -> bool | None:
    """Ignore already-dead zombies; uncertainty is never a stopped confirmation."""
    try:
        result = subprocess.run(
            ["/bin/ps", "-axo", "pgid=,stat="],
            capture_output=True,
            timeout=2,
            check=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        for line in result.stdout.decode().splitlines():
            group, state = line.split()
            if int(group) == pgid and not state.startswith("Z"):
                return True
        return False
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _kill_group(process: subprocess.Popen[bytes]) -> bool:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        return False
    for _ in range(20):
        if _group_live(process.pid) is False:
            return True
        time.sleep(0.05)
    return False


def _directory_size(path: Path, limit: int) -> int:
    """Bounded, descriptor-relative metadata scan; never follow worker symlinks.

    Complexity or unreadable metadata trips the advisory watchdog conservatively.
    A worker must not be able to stall/kill the host deadline supervisor by racing
    directory mutations, withholding permissions, or creating unbounded entries.
    """
    total = 0
    visited = 0
    deadline = time.monotonic() + 0.05
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors: list[int] = []
    try:
        descriptors.append(os.open(path, flags))
        while descriptors:
            descriptor = descriptors.pop()
            try:
                with os.scandir(descriptor) as entries:
                    for entry in entries:
                        visited += 1
                        if visited > 10000 or time.monotonic() > deadline:
                            return limit + 1
                        try:
                            info = entry.stat(follow_symlinks=False)
                            if stat.S_ISDIR(info.st_mode):
                                if len(descriptors) >= 64:
                                    return limit + 1
                                descriptors.append(os.open(entry.name, flags, dir_fd=descriptor))
                            elif stat.S_ISREG(info.st_mode):
                                total += info.st_size
                        except FileNotFoundError:
                            continue
                        if total > limit:
                            return total
            finally:
                os.close(descriptor)
        return total
    except OSError:
        return limit + 1
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def _supervise(job_dir: Path) -> None:
    """Trusted detached supervisor. Candidate bytes never execute in controller."""
    spec = json.loads((job_dir / "dispatch.json").read_text())
    cancelled = False

    def request_cancel(_signal: int, _frame: Any) -> None:
        nonlocal cancelled
        cancelled = True

    signal.signal(signal.SIGTERM, request_cancel)
    _json_write(
        job_dir / "supervisor.json", {"pid": os.getpid(), "identity": _identity(os.getpid())}
    )
    (job_dir / "input.json").write_text(json.dumps(spec["request"]))
    with (job_dir / "input.json").open("rb") as source:
        child = subprocess.Popen(
            spec["argv"],
            cwd=spec["workspace"],
            stdin=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "PYTHONHASHSEED": "0"},
        )
    _json_write(job_dir / "child.json", {"pid": child.pid, "identity": _identity(child.pid)})
    started = time.monotonic()
    streams = selectors.DefaultSelector()
    assert child.stdout is not None and child.stderr is not None
    streams.register(child.stdout, selectors.EVENT_READ, "stdout")
    streams.register(child.stderr, selectors.EVENT_READ, "stderr")
    output: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    reason = "exit"
    confirmed = False
    while True:
        if cancelled or time.monotonic() - started > spec["timeout_seconds"]:
            reason = "cancelled" if cancelled else "timeout"
            confirmed = _kill_group(child)
            break
        if (
            _directory_size(Path(spec["workspace"]), spec["storage_mib"] * 1048576)
            > spec["storage_mib"] * 1048576
        ):
            reason = "advisory storage watchdog exceeded"
            confirmed = _kill_group(child)
            break
        for key, _ in streams.select(0.02):
            data = os.read(key.fileobj.fileno(), 65536)
            if not data:
                streams.unregister(key.fileobj)
                continue
            output[key.data].extend(data)
        if sum(map(len, output.values())) > MAX_OUTPUT:
            reason = "output limit exceeded"
            confirmed = _kill_group(child)
            break
        if child.poll() is not None and not streams.get_map():
            # A fixture that leaves descendants behind is not complete.
            confirmed = _kill_group(child)
            break
    streams.close()
    child.stdout.close()
    child.stderr.close()
    _json_write(
        job_dir / "result.json",
        {
            "returncode": child.returncode,
            "stdout": base64.b64encode(bytes(output["stdout"][:MAX_OUTPUT])).decode(),
            "stderr": base64.b64encode(bytes(output["stderr"][:MAX_OUTPUT])).decode(),
            "stopped": confirmed,
            "detail": reason,
        },
    )


def _docker_supervise(job_dir: Path) -> None:
    """Host-side deadline survives controller loss and cannot be signalled by workers."""
    dispatch = json.loads((job_dir / "dispatch.json").read_text())
    command = [dispatch["docker"], "container"]
    _json_write(job_dir / "supervisor.json", {"pid": os.getpid()})
    started = time.monotonic()
    reason = "exit"
    try:
        with _guard_lock(job_dir):
            if (job_dir / "cancelled").exists():
                _json_write(job_dir / "result.json", {"detail": "cancelled before start"})
                return
            result = subprocess.run(
                [*command, "start", dispatch["name"]], capture_output=True, timeout=15, check=False
            )
        if result.returncode:
            _json_write(job_dir / "result.json", {"detail": "container start failed"})
            return
        while True:
            result = subprocess.run(
                [*command, "inspect", dispatch["name"]],
                capture_output=True,
                timeout=15,
                check=False,
            )
            if result.returncode:
                # A daemon outage is not a stopped confirmation. Continue trying
                # to cancel until the backend can positively confirm termination.
                reason = "daemon unavailable; stop confirmation pending"
            else:
                state = json.loads(result.stdout)[0]["State"]
                if not state["Running"]:
                    if state["Status"] in {"exited", "dead", "created"}:
                        break
                if (job_dir / "cancelled").exists():
                    reason = "cancelled"
                elif time.monotonic() - started > dispatch["timeout"]:
                    reason = "host wall deadline exceeded"
                elif (
                    _directory_size(Path(dispatch["workspace"]), dispatch["storage_bytes"])
                    > dispatch["storage_bytes"]
                ):
                    reason = "advisory storage watchdog exceeded"
                else:
                    time.sleep(0.05)
                    continue
            _json_write(job_dir / "stop_request.json", {"detail": reason})
            subprocess.run(
                [*command, "kill", "--signal", "KILL", dispatch["name"]],
                capture_output=True,
                timeout=15,
                check=False,
            )
            # A container removed by the trusted controller has already passed
            # its separate positive stopped check.
            if result.returncode and b"No such container" in result.stderr:
                break
            time.sleep(0.05)
    except (OSError, ValueError, subprocess.SubprocessError):
        reason = "host supervisor failed; reservation requires reconciliation"
    _json_write(job_dir / "result.json", {"detail": reason})


class DevelopmentDriver:
    """Exact known fixtures only; no OS isolation, hard memory, CPU or PID claim."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._supervisors: dict[str, subprocess.Popen[bytes]] = {}

    def probe(self) -> dict[str, Any]:
        return {
            "driver": "development",
            "available": True,
            "profile": "development",
            "adversarial_isolation": False,
            "network_denied": False,
            "cpu_hard": False,
            "memory_hard": False,
            "pids_hard": False,
            "storage_hard": False,
            "storage_enforcement": "advisory polling watchdog",
            "wall_time": "detached trusted supervisor",
            "gpu": False,
            "deployment_verified": False,
        }

    def _directory(self, job_id: str) -> Path:
        if not _ID.fullmatch(job_id):
            raise DriverError("invalid job identity")
        return self.state_dir / job_id

    def launch(self, spec: JobSpec) -> str:
        if spec.required_profile != "development" or spec.require_hard_storage:
            raise DriverError("development driver cannot satisfy requested security property")
        from kestrel.fixtures import is_trusted_workspace

        if spec.argv != (sys.executable, "-I", "-B", "worker.py"):
            raise DriverError("development driver only executes the known fixture argument vector")
        if not is_trusted_workspace(spec.workspace):
            raise DriverError("development driver refuses unknown or modified fixture code")
        if spec.request.get("output_directory") != "output":
            raise DriverError("trusted fixture output must be the fixed output directory")
        output = spec.workspace / "output"
        if output.exists():
            if not output.is_dir() or any(
                item.name != "result.json"
                or item.is_symlink()
                or not item.is_file()
                or item.stat().st_nlink != 1
                for item in output.iterdir()
            ):
                raise DriverError("trusted fixture refuses unsafe existing outputs")
        if self.state_dir == spec.workspace.resolve() or self.state_dir.is_relative_to(
            spec.workspace.resolve()
        ):
            raise DriverError("workspace must not contain driver state")
        directory = self._directory(spec.attempt_id)
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            dispatch = directory / "dispatch.json"
            if not dispatch.exists() or json.dumps(
                json.loads(dispatch.read_text()), sort_keys=True
            ) != json.dumps(_spec_data(spec), sort_keys=True):
                raise DriverError(
                    "attempt already exists or dispatch outcome is uncertain"
                ) from None
            return spec.attempt_id
        _json_write(directory / "dispatch.json", _spec_data(spec))
        # Persist the tombstone first. A crash before/after Popen never permits a
        # second blind launch; an incomplete dispatch remains UNKNOWN.
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                str(Path(__file__).resolve()),
                "--supervise",
                str(directory),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        )
        self._supervisors[spec.attempt_id] = process
        return spec.attempt_id

    def inspect(self, job_id: str) -> JobStatus:
        directory = self._directory(job_id)
        if not directory.exists():
            return JobStatus(job_id, "absent", stopped=True)
        result = directory / "result.json"
        if result.exists():
            data = json.loads(result.read_text())
            process = self._supervisors.get(job_id)
            if process is not None:
                process.poll()
            return JobStatus(
                job_id,
                "stopped" if data["stopped"] else "unknown",
                data["returncode"],
                base64.b64decode(data["stdout"]),
                base64.b64decode(data["stderr"]),
                data["stopped"],
                data["detail"],
            )
        supervisor = directory / "supervisor.json"
        if supervisor.exists():
            data = json.loads(supervisor.read_text())
            if data["identity"] is not None and _identity(data["pid"]) == data["identity"]:
                return JobStatus(job_id, "running")
        return JobStatus(
            job_id, "unknown", detail="dispatch/supervisor uncertain; reservation must remain held"
        )

    def reconcile(self, attempt_id: str) -> JobStatus:
        return self.inspect(attempt_id)

    def wait(self, job_id: str, timeout: float = 125) -> JobStatus:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.inspect(job_id)
            if status.stopped:
                return status
            time.sleep(0.02)
        return JobStatus(job_id, "unknown", detail="wait deadline; reservation remains held")

    def cancel(self, job_id: str) -> JobStatus:
        status = self.inspect(job_id)
        if status.stopped:
            return status
        supervisor = self._directory(job_id) / "supervisor.json"
        if supervisor.exists():
            data = json.loads(supervisor.read_text())
            if data["identity"] is not None and _identity(data["pid"]) == data["identity"]:
                try:
                    os.kill(data["pid"], signal.SIGTERM)
                except ProcessLookupError:
                    pass
                return self.wait(job_id, timeout=5)
        return JobStatus(
            job_id, "unknown", detail="cannot confirm supervisor identity; reservation remains held"
        )


# Runs inside the container. The wall deadline survives controller loss; exiting
# PID 1's child ends the container namespace and all candidate descendants.
_CONTAINER_BOOTSTRAP = """
import base64,json,os,signal,subprocess,sys
payload=json.loads(base64.b64decode(sys.argv[1]))
def timeout(*_):
    os.write(2,b'kestrel wall deadline exceeded\\n')
    os._exit(124)
signal.signal(signal.SIGALRM,timeout)
signal.setitimer(signal.ITIMER_REAL,payload['timeout'])
process=subprocess.Popen(payload['argv'],stdin=subprocess.PIPE,env={'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C','HOME':'/tmp'})
process.communicate(json.dumps(payload['request']).encode())
sys.exit(process.returncode)
"""


class DockerDriver:
    """Linux containers with explicit capabilities, never automatic fallback.

    Storage for a writable bind mount has an advisory watchdog, not a hard quota.
    Consequently contracts requiring a hard workspace cap always fail closed.
    A read-only probe describes runtime availability, not completed acceptance.
    """

    def __init__(self, image: str, workspace_root: Path, *, docker: str = "docker"):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise DriverError("Docker requires an exact locally available image SHA256")
        self.image = image
        self.workspace_root = Path(workspace_root).resolve(strict=True)
        if self.workspace_root in {Path("/"), Path.home().resolve()}:
            raise DriverError("a dedicated external workspace root is required")
        self.docker = shutil.which(docker) or docker
        self._specs: dict[str, JobSpec] = {}
        self._guards: dict[str, subprocess.Popen[bytes]] = {}
        self.state_dir = self.workspace_root / ".kestrel-driver"

    def _call(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        try:
            result = subprocess.run(
                [self.docker, *args], capture_output=True, timeout=15, check=False
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise DriverError(f"Docker operation unavailable: {type(error).__name__}") from error
        if check and result.returncode:
            raise DriverError(
                f"Docker operation failed: {result.stderr.decode(errors='replace')[:2000]}"
            )
        return result

    def probe(self) -> dict[str, Any]:
        try:
            info = json.loads(self._call(["info", "--format", "{{json .}}"]).stdout)
            image_data = json.loads(self._call(["image", "inspect", self.image]).stdout)[0]
        except (DriverError, ValueError, KeyError, IndexError):
            return {
                "driver": "docker",
                "available": False,
                "deployment_verified": False,
                "reason": "Docker daemon or pinned local image unavailable",
            }
        supported = info.get("OSType") == "linux" and all(
            info.get(key) for key in ("MemoryLimit", "SwapLimit", "CpuCfsQuota", "PidsLimit")
        )
        supported = supported and not image_data.get("Config", {}).get("Volumes")
        return {
            "driver": "docker",
            "available": supported,
            "profile": "isolated-local",
            "host_platform": platform.system(),
            "runtime_os": info.get("OSType"),
            "runtime_version": info.get("ServerVersion"),
            "runtime_kernel": info.get("KernelVersion"),
            "runtime_arch": info.get("Architecture"),
            "image": self.image,
            "cpu_hard": bool(info.get("CpuCfsQuota")),
            "memory_hard": bool(info.get("MemoryLimit")),
            "pids_hard": bool(info.get("PidsLimit")),
            "storage_hard": False,
            "storage_enforcement": "advisory polling watchdog for writable workspace; bounded tmpfs",
            "network_configuration": "none",
            "wall_time": "detached host supervisor plus in-container early deadline",
            "gpu": False,
            "deployment_verified": False,
            "verification": "runtime declaration only; adversarial acceptance must be run separately",
        }

    @staticmethod
    def _name(attempt_id: str) -> str:
        if not _ID.fullmatch(attempt_id):
            raise DriverError("invalid attempt identity")
        return "kestrel-" + hashlib.sha256(attempt_id.encode()).hexdigest()[:40]

    def _lookup(self, name: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"kestrel-[0-9a-f]{40}", name):
            raise DriverError("invalid Docker job identity")
        result = self._call(["container", "inspect", name], check=False)
        if result.returncode:
            if b"No such container" in result.stderr or b"No such object" in result.stderr:
                return None
            raise DriverError("container state unavailable; retain reservations")
        return json.loads(result.stdout)[0]

    def launch(self, spec: JobSpec) -> str:
        if spec.require_hard_storage:
            raise DriverError("hard writable workspace storage quota unavailable; no fallback")
        if not self.probe().get("available"):
            raise DriverError("required Linux container capabilities unavailable; no fallback")
        workspace = spec.workspace.resolve(strict=True)
        protected = (Path.home().resolve(), Path(__file__).resolve(), self.state_dir)
        if any(path == workspace or path.is_relative_to(workspace) for path in protected):
            raise DriverError("workspace would expose home, framework code, or driver state")
        if workspace == self.workspace_root or not workspace.is_relative_to(self.workspace_root):
            raise DriverError("workspace must be a dedicated child of the allowed external root")
        if "," in str(workspace) or "\n" in str(workspace):
            raise DriverError("workspace path is not representable safely as a Docker mount")
        name = self._name(spec.attempt_id)
        previous = self._lookup(name)
        if previous is not None:
            if previous["Config"].get("Labels", {}).get("kestrel.spec") != _spec_digest(spec):
                raise DriverError("attempt identity already bound to a different job")
            if previous["State"]["Status"] != "created":
                self._specs[name] = spec
                return name
        else:
            payload = base64.b64encode(
                json.dumps(
                    {
                        "argv": spec.argv,
                        "request": spec.request,
                        "timeout": spec.timeout_seconds,
                    }
                ).encode()
            ).decode()
            image_info = json.loads(self._call(["image", "inspect", self.image]).stdout)[0]
            environment = []
            for variable in image_info["Config"].get("Env", []):
                environment.extend(["--env", variable.split("=", 1)[0] + "="])
            self._call(
                [
                    "container",
                    "create",
                    "--pull",
                    "never",
                    "--name",
                    name,
                    "--label",
                    "kestrel.attempt=" + spec.attempt_id,
                    "--label",
                    "kestrel.spec=" + _spec_digest(spec),
                    "--label",
                    "kestrel.workspace=" + str(workspace),
                    "--label",
                    "kestrel.storage_bytes=" + str(spec.storage_mib * 1048576),
                    "--network",
                    "none",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--cgroupns",
                    "private",
                    "--user",
                    "65534:65534",
                    "--init",
                    "--no-healthcheck",
                    "--cpus",
                    str(spec.cpu),
                    "--memory",
                    str(spec.memory_mib * 1048576),
                    "--memory-swap",
                    str(spec.memory_mib * 1048576),
                    "--pids-limit",
                    str(spec.pids),
                    "--ulimit",
                    "nofile=128:128",
                    "--shm-size",
                    "1048576",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,noexec,size=8388608,mode=1777",
                    "--log-driver",
                    "local",
                    "--log-opt",
                    "max-size=1m",
                    "--log-opt",
                    "max-file=1",
                    "--log-opt",
                    "compress=false",
                    "--mount",
                    f"type=bind,src={workspace},dst=/workspace,bind-recursive=disabled",
                    "--workdir",
                    "/workspace",
                    *environment,
                    "--entrypoint",
                    "/usr/local/bin/python",
                    self.image,
                    "-I",
                    "-B",
                    "-c",
                    _CONTAINER_BOOTSTRAP,
                    payload,
                ]
            )
        self.state_dir.mkdir(mode=0o700, exist_ok=True)
        guard_dir = self.state_dir / name
        try:
            guard_dir.mkdir(mode=0o700)
        except FileExistsError:
            # A tombstone preceding supervisor creation closes the ambiguous
            # dispatch window. Never blindly create a second supervisor/start.
            self._specs[name] = spec
            return name
        _json_write(
            guard_dir / "dispatch.json",
            {
                "docker": self.docker,
                "name": name,
                "workspace": str(workspace),
                "storage_bytes": spec.storage_mib * 1048576,
                "timeout": spec.timeout_seconds,
            },
        )
        cli_env = {
            key: os.environ[key]
            for key in ("HOME", "PATH", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
            if key in os.environ
        }
        self._guards[name] = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                str(Path(__file__).resolve()),
                "--docker-supervise",
                str(guard_dir),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=cli_env,
        )
        self._specs[name] = spec
        return name

    def inspect(self, job_id: str) -> JobStatus:
        try:
            data = self._lookup(job_id)
        except DriverError as error:
            return JobStatus(job_id, "unknown", detail=str(error))
        if data is None:
            return JobStatus(job_id, "absent", stopped=True)
        state = data["State"]
        if state["Running"]:
            labels = data["Config"].get("Labels", {})
            workspace = Path(labels["kestrel.workspace"])
            cap = int(labels["kestrel.storage_bytes"])
            if (
                workspace.is_relative_to(self.workspace_root)
                and _directory_size(workspace, cap) > cap
            ):
                return self.cancel(job_id, detail="advisory storage watchdog exceeded")
            return JobStatus(job_id, "running")
        if state["Status"] not in {"exited", "dead"}:
            guard_dir = self.state_dir / job_id
            pending_stopped = (
                state["Status"] == "created"
                and state.get("Pid") == 0
                and ((guard_dir / "cancelled").exists() or (guard_dir / "result.json").exists())
            )
            return JobStatus(
                job_id,
                "stopped" if pending_stopped else "unknown",
                returncode=state.get("ExitCode") if pending_stopped else None,
                stopped=pending_stopped,
                detail="created/paused/removing container; reconcile before retry",
            )
        logs = self._call(["container", "logs", job_id], check=False)
        if len(logs.stdout) + len(logs.stderr) > MAX_OUTPUT:
            return JobStatus(
                job_id,
                "stopped",
                state["ExitCode"],
                stopped=True,
                detail="output limit exceeded; protocol output rejected",
            )
        detail = "OOM killed" if state.get("OOMKilled") else "exit"
        guard_result = self.state_dir / job_id / "result.json"
        guard_stop = self.state_dir / job_id / "stop_request.json"
        if guard_result.exists() and detail == "exit":
            detail = json.loads(guard_result.read_text())["detail"]
        elif guard_stop.exists() and detail == "exit":
            detail = json.loads(guard_stop.read_text())["detail"]
        return JobStatus(
            job_id,
            "stopped",
            state["ExitCode"],
            logs.stdout,
            logs.stderr,
            True,
            detail,
        )

    def reconcile(self, attempt_id: str) -> JobStatus:
        return self.inspect(self._name(attempt_id))

    def wait(self, job_id: str, timeout: float = 125) -> JobStatus:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.inspect(job_id)
            if status.stopped:
                return status
            time.sleep(0.05)
        return JobStatus(job_id, "unknown", detail="wait deadline; reservation remains held")

    def cancel(self, job_id: str, *, detail: str = "cancelled") -> JobStatus:
        # Fence starts before looking for a running container. Otherwise a
        # pending detached supervisor could start after cancellation returned.
        guard_dir = self.state_dir / job_id
        self._lookup(job_id)  # Validate identity before deriving a writable path.
        with _guard_lock(guard_dir):
            (guard_dir / "cancelled").touch()
        data = self._lookup(job_id)
        if data is None:
            return JobStatus(job_id, "absent", stopped=True)
        if data["State"]["Running"]:
            self._call(["container", "kill", "--signal", "KILL", job_id], check=False)
            remaining = self._lookup(job_id)
            if remaining is not None and remaining["State"]["Running"]:
                return JobStatus(
                    job_id,
                    "unknown",
                    detail="container stop not confirmed; reservation remains held",
                )
        status = self.inspect(job_id)
        if status.stopped:
            return JobStatus(
                job_id, "stopped", status.returncode, status.stdout, status.stderr, True, detail
            )
        return JobStatus(
            job_id, "unknown", detail="container stop not confirmed; reservation remains held"
        )

    def remove(self, job_id: str) -> None:
        """Cleanup only a confirmed stopped Kestrel container; never force-remove."""
        status = self.inspect(job_id)
        if not status.stopped:
            raise DriverError("cannot remove an unconfirmed job")
        if status.state != "absent":
            self._call(["container", "rm", job_id])
        guard = self._guards.get(job_id)
        if guard is not None:
            try:
                guard.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--supervise":
        _supervise(Path(sys.argv[2]))
    elif len(sys.argv) == 3 and sys.argv[1] == "--docker-supervise":
        _docker_supervise(Path(sys.argv[2]))
    else:
        raise SystemExit("internal fixture supervisor only")
