"""Record actual test outcomes against the unchanged acceptance inventory."""

import datetime
import os
import platform
import shlex
import sys
import uuid
from pathlib import Path

import pytest
from _pytest.junitxml import xml_key


def pytest_collection_modifyitems(items):
    for item in items:
        for marker in item.iter_markers("acceptance"):
            item.user_properties.append(("acceptance", marker.args[0]))
        # Messaging conditions are a separate proposed inventory. They must not
        # enter the pinned `acceptance` property, where an unknown ID becomes a
        # release-gate integrity diagnostic.
        for marker in item.iter_markers("messaging"):
            item.user_properties.append(("messaging_acceptance", marker.args[0]))


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session, exitstatus):
    xml = session.config.stash.get(xml_key, None)
    if xml is not None:
        properties = {
            "kestrel_run_id": str(uuid.uuid4()),
            "kestrel_command": shlex.join([sys.executable, *sys.argv]),
            "kestrel_exit_code": int(exitstatus),
            "kestrel_run_completed_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "kestrel_runtime_platform": os.environ.get("KESTREL_TEST_RUNTIME", platform.platform()),
            "kestrel_platform": platform.platform(),
            "kestrel_profile": os.environ.get("KESTREL_TEST_PROFILE", "development"),
            "kestrel_scope": os.environ.get("KESTREL_TEST_SCOPE", "core"),
            "kestrel_source_revision": os.environ.get("KESTREL_SOURCE_REVISION", "uncommitted"),
        }
        for key, value in properties.items():
            xml.add_global_property(key, value)


@pytest.fixture(autouse=True)
def external_fixture_directory(request, tmp_path_factory):
    base = tmp_path_factory.getbasetemp().resolve()
    checkout = Path(__file__).resolve().parents[1]
    if base.is_relative_to(checkout):
        pytest.fail("Synthetic projects/runtime must be generated outside the framework checkout")
