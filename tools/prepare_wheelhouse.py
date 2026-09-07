"""Prepare hash-verified runtime dependency wheels before offline acceptance.

Requires the development environment's ``packaging`` module. This setup command
downloads compatible wheels from the approved public Python package registry;
it never imports project code, executes dependency hooks, or builds source
distributions. Offline tests consume the resulting external wheelhouse.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import tempfile
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

MAX_WHEEL_BYTES = 32 * 1024**2


def permitted_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "files.pythonhosted.org"
        and not parsed.username
    )


class RegistryOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        if not permitted_url(new_url):
            raise ValueError("Wheel download attempted an unapproved registry redirect")
        return super().redirect_request(request, file, code, message, headers, new_url)


def prepare(project_path: Path, lock_path: Path, destination: Path, *, download: bool) -> dict:
    checkout = Path(__file__).resolve().parents[1]
    destination = destination.resolve()
    if destination.is_relative_to(checkout) or checkout.is_relative_to(destination):
        raise ValueError("Prepare dependency wheels outside the framework checkout")
    project = tomllib.loads(project_path.read_text())
    lock_bytes = lock_path.read_bytes()
    lock = tomllib.loads(lock_bytes.decode())
    by_name = {}
    for package in lock["package"]:
        name = canonicalize_name(package["name"])
        if name in by_name:
            raise ValueError("This helper requires one locked version per dependency")
        by_name[name] = package
    pending = []
    for text in project["project"]["dependencies"]:
        requirement = Requirement(text)
        if requirement.marker is None or requirement.marker.evaluate():
            pending.append(canonicalize_name(requirement.name))
    required = {}
    while pending:
        name = pending.pop()
        if name in required:
            continue
        package = by_name[name]
        required[name] = package
        for dependency in package.get("dependencies", []):
            if "marker" not in dependency or Marker(dependency["marker"]).evaluate():
                pending.append(canonicalize_name(dependency["name"]))
    compatible = {tag: index for index, tag in enumerate(sys_tags())}
    selected = []
    for name, package in sorted(required.items()):
        choices = []
        for wheel in package.get("wheels", []):
            filename = Path(urllib.parse.urlparse(wheel["url"]).path).name
            wheel_name, version, _, tags = parse_wheel_filename(filename)
            ranks = [compatible[tag] for tag in tags if tag in compatible]
            if canonicalize_name(wheel_name) != name or str(version) != package["version"]:
                raise ValueError("Locked wheel identity differs from the dependency record")
            if ranks:
                choices.append((min(ranks), filename, wheel))
        if not choices:
            raise ValueError(f"No compatible prebuilt wheel is pinned for {name}")
        _, filename, wheel = min(choices)
        if not permitted_url(wheel["url"]):
            raise ValueError("Only the approved public Python wheel registry is permitted")
        expected = wheel["hash"].removeprefix("sha256:")
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            raise ValueError("A locked SHA256 digest is required")
        size = wheel["size"]
        if type(size) is not int or not 0 < size <= MAX_WHEEL_BYTES:
            raise ValueError("Dependency wheel exceeds setup download bounds")
        selected.append(
            {
                "name": name,
                "version": package["version"],
                "filename": filename,
                "url": wheel["url"],
                "sha256": expected,
                "size": size,
            }
        )
    destination.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(RegistryOnlyRedirects())
    for wheel in selected:
        target = destination / wheel["filename"]
        if target.exists():
            content = target.read_bytes()
        elif download:
            with opener.open(wheel["url"], timeout=30) as response:
                content = response.read(wheel["size"] + 1)
        else:
            raise ValueError("Missing wheel; use --download during authorized dependency setup")
        if len(content) != wheel["size"] or hashlib.sha256(content).hexdigest() != wheel["sha256"]:
            raise ValueError(f"Locked dependency size/hash mismatch: {wheel['filename']}")
        if not target.exists():
            descriptor, temporary = tempfile.mkstemp(prefix=".wheel-", dir=destination)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                if Path(temporary).exists():
                    Path(temporary).unlink()
    result = {
        "schema_version": "0.1",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "wheelhouse": str(destination),
        "wheels": selected,
        "total_bytes": sum(wheel["size"] for wheel in selected),
    }
    (destination / "wheelhouse.json").write_text(json.dumps(result, sort_keys=True, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--lock", type=Path, default=Path("uv.lock"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--download", action="store_true", help="allow public registry downloads during setup"
    )
    arguments = parser.parse_args()
    print(
        json.dumps(
            prepare(
                arguments.project, arguments.lock, arguments.output, download=arguments.download
            ),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
