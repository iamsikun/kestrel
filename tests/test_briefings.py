"""Read-only briefing tests.

These check that a briefing reproduces the authoritative report interpretation
and never mutates a research store. They do not establish that the underlying
scientific conclusions are correct, and a passing briefing says nothing about
transport, deployment, or operator attention.
"""

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

import pytest

from kestrel.application import Lab, framework_root
from kestrel.artifacts import ArtifactError
from kestrel.briefings import (
    UNAVAILABLE,
    brief,
    briefing_digest,
    build_briefing,
    evidence_epoch,
    render_markdown,
    render_plain,
    safe_text,
)
from kestrel.cli import main, parse_instant
from kestrel.contracts import canonical
from kestrel.fixtures import generate
from kestrel.reporting import build_report
from kestrel.sources import ControllerSource, LabSources, SourceUnavailable

BIDI_OVERRIDE = "‮"


def state_fingerprint(root: Path) -> dict:
    """Identity of every research state file, used to prove read-only access.

    SQLite's write-ahead log and shared-memory index are excluded: mapping the
    `-shm` index is unavoidable for *any* reader of a live WAL database, so its
    modification time changes even though no database content does. Logical
    content is checked separately by `logical_fingerprint`, which is the
    invariant that actually matters.
    """
    record = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink() and not path.name.endswith(
                ("-shm", "-wal")):
            stat = path.stat()
            record[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns)
    return record


def logical_fingerprint(root: Path) -> str:
    """Hash every row of every research table plus every stored artifact byte."""
    parts = []
    for database in sorted(root.rglob("*.sqlite")):
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
            tables = sorted(row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"))
            for table in tables:
                rows = sorted(repr(tuple(row)) for row in
                              connection.execute(f"SELECT * FROM {table}"))
                parts.append(f"{database.name}:{table}:" + "|".join(rows))
    for artifact in sorted((root / "runtime" / "artifacts" / "objects").glob("*")):
        parts.append(f"object:{artifact.name}:"
                     + hashlib.sha256(artifact.read_bytes()).hexdigest())
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


@pytest.fixture
def completed_lab(tmp_path):
    manifests = generate(tmp_path / "external-projects", framework_root())
    with Lab.initialize(tmp_path / "external-lab") as lab:
        record = lab.register(manifests[0], snapshot_dirty=True)
        campaign = lab.propose(record["project_id"], "Preserve this original synthetic brief.")
        approval = lab.approve(campaign, lab.controller.campaign(campaign)["digest"],
                               (lab.root / "operator.token").read_text())
        report = lab.run(campaign, approval)
        root = lab.root
    return root, campaign, report


@pytest.fixture
def pending_lab(tmp_path):
    """A frozen campaign with tasks and no approval yet."""
    manifests = generate(tmp_path / "external-projects", framework_root())
    with Lab.initialize(tmp_path / "external-lab") as lab:
        record = lab.register(manifests[1], snapshot_dirty=True)
        campaign = lab.propose(record["project_id"], "Awaiting authorization.")
        root = lab.root
    return root, campaign


@pytest.mark.messaging("N-A09")
def test_briefing_reproduces_the_authoritative_report_exactly(completed_lab):
    root, campaign, report = completed_lab
    with LabSources(root) as sources:
        recomputed = build_report(campaign, sources.controller.campaign(campaign),
                                  sources.evidence, sources.controller.attempts(campaign),
                                  sources.controller.budget_used(campaign))
    assert recomputed == report
    with Lab(root) as lab:
        assert lab.report(campaign) == recomputed


@pytest.mark.messaging("N-A09")
def test_negative_finding_is_reported_as_a_successful_outcome(completed_lab):
    root, campaign, report = completed_lab
    assert report["finding"] == "not_supported"
    record = brief(root)
    outcome = next(row for row in record["sections"]["verified_outcomes"]
                   if row["campaign_id"] == campaign)["outcome"]
    assert outcome == {"execution": "succeeded", "validity": "valid",
                       "finding": "not_supported", "assurance": "independently_recomputed"}
    text = render_markdown(record)
    assert "execution succeeded; protocol valid; finding not_supported" in text
    # The four axes are never collapsed into a single verdict word.
    assert "failed" not in text.lower().split("## integrity")[0]


@pytest.mark.messaging("N-A09")
def test_every_number_carries_a_named_derivation_and_missing_telemetry_is_labelled(
        completed_lab):
    root, _, _ = completed_lab
    record = brief(root)
    for row in record["sections"]["reservations"]:
        assert row["derivation"] in record["derivations"]
    for item in record["sections"]["awaiting_you"] + record["sections"]["integrity"]:
        assert item["derivation"] in record["derivations"]
    assert record["unavailable"] == list(UNAVAILABLE)
    text = render_markdown(record)
    assert "Reserved worst case per attempt, not measured consumption." in text
    for absent in ("Monetary cost", "Host telemetry", "Operator reading"):
        assert absent in text


@pytest.mark.messaging("N-A09")
def test_briefing_does_not_write_to_any_research_state(completed_lab):
    root, _, _ = completed_lab
    before_files = state_fingerprint(root)
    before_content = logical_fingerprint(root)
    for form in ("json", "markdown", "plain"):
        brief(root, form=form)
    assert state_fingerprint(root) == before_files
    assert logical_fingerprint(root) == before_content


@pytest.mark.messaging("N-A09")
def test_read_only_source_refuses_to_mutate_even_when_asked(completed_lab):
    root, _, _ = completed_lab
    with ControllerSource(root / "runtime" / "controller.sqlite") as source:
        for statement in ("UPDATE campaigns SET state='EXPLORING'",
                          "INSERT INTO events(campaign_id,kind,subject,detail,created_at) "
                          "VALUES (NULL,'forged','x','{}',0)",
                          "CREATE TABLE intrusion(x TEXT)",
                          "PRAGMA user_version=99"):
            with pytest.raises(sqlite3.Error):
                source._db.execute(statement)


@pytest.mark.messaging("N-A09")
def test_pending_campaign_reports_awaiting_approval_without_inventing_an_outcome(pending_lab):
    root, campaign = pending_lab
    record = brief(root)
    awaiting = [item for item in record["sections"]["awaiting_you"]
                if item["campaign_id"] == campaign]
    assert [item["condition"] for item in awaiting] == ["approval_needed"]
    assert awaiting[0]["unfinished_tasks"] == 3
    assert record["sections"]["verified_outcomes"] == []
    summary = next(row for row in record["campaigns"] if row["campaign_id"] == campaign)
    assert summary["state"] == "FROZEN"


@pytest.mark.messaging("N-A14")
def test_invalidated_evidence_downgrades_the_briefing_instead_of_repeating_a_win(
        completed_lab):
    root, campaign, report = completed_lab
    healthy = brief(root)
    assert healthy["sections"]["verified_outcomes"][0]["outcome"]["finding"] == "not_supported"
    with Lab(root) as lab:
        lab.store.invalidate(report["evidence"][0]["digest"], "evaluator withdrawn")
    corrected = brief(root)
    outcome = corrected["sections"]["verified_outcomes"][0]["outcome"]
    assert outcome["validity"] == "invalid"
    assert outcome["finding"] == "inconclusive"
    assert outcome["assurance"] == "unverified"
    corrections = [item for item in corrected["sections"]["integrity"]
                   if item["condition"] == "evidence_correction"]
    assert corrections and corrections[0]["reason"] == "evaluator withdrawn"
    assert corrected["content_digest"] != healthy["content_digest"]
    assert campaign in {row["campaign_id"] for row in corrected["campaigns"]}


@pytest.mark.messaging("N-A14")
def test_one_invalidation_is_grouped_rather_than_repeated_per_descendant(completed_lab):
    root, _, report = completed_lab
    with Lab(root) as lab:
        analysis = report["evidence"][0]["digest"]
        ancestor = lab.store.get(analysis)["lineage"][0]
        affected = lab.store.invalidate(ancestor, "input dataset withdrawn")
    assert len(affected) > 1
    corrections = [item for item in brief(root)["sections"]["integrity"]
                   if item["condition"] == "evidence_correction"]
    assert len(corrections) == 1
    assert corrections[0]["affected"] == len(affected)


@pytest.mark.messaging("N-A14")
def test_tampered_evidence_becomes_an_integrity_item_not_a_certified_result(completed_lab):
    root, campaign, report = completed_lab
    with Lab(root) as lab:
        identity = report["evidence"][0]["digest"]
        artifact = Path(lab.store.get(identity)["path"])
        invented = dict(report["evidence"][0]["analysis"])
        invented["treatment"] = -999999.0
        invented["finding"] = "supported_in_scope"
        artifact.chmod(0o600)
        artifact.write_bytes(canonical(invented))
        with pytest.raises(ArtifactError, match="hash mismatch"):
            lab.report(campaign)
    record = brief(root)
    outcome = record["sections"]["verified_outcomes"][0]
    assert outcome["verification"] == "failed"
    assert outcome["outcome"]["finding"] == "unavailable"
    conditions = {item["condition"] for item in record["sections"]["integrity"]}
    assert "verification_failed" in conditions
    assert "supported_in_scope" not in json.dumps(record)


@pytest.mark.messaging("N-A14")
def test_deleted_evidence_tombstone_is_reported_as_unavailable(completed_lab):
    root, _, report = completed_lab
    with Lab(root) as lab:
        identity = report["evidence"][0]["digest"]
        lab.store.set_retention(identity, False)
        lab.store.collect(identity, reason="retention window elapsed")
    record = brief(root)
    assert record["sections"]["verified_outcomes"][0]["verification"] == "failed"
    assert record["sections"]["verified_outcomes"][0]["outcome"]["execution"] == "unavailable"


@pytest.mark.messaging("N-A09")
def test_unavailable_sources_fail_closed_rather_than_reporting_health(tmp_path):
    with pytest.raises(SourceUnavailable):
        brief(tmp_path / "absent-lab")
    empty = tmp_path / "not-a-lab"
    empty.mkdir()
    with pytest.raises(SourceUnavailable):
        brief(empty)


@pytest.mark.messaging("N-A09")
def test_unsupported_controller_schema_is_refused(completed_lab, tmp_path):
    root, _, _ = completed_lab
    copy = tmp_path / "copy.sqlite"
    with sqlite3.connect(root / "runtime" / "controller.sqlite") as original, \
            sqlite3.connect(copy) as target:
        original.backup(target)
        target.execute("PRAGMA user_version=99")
    with pytest.raises(SourceUnavailable, match="schema version"):
        ControllerSource(copy)


@pytest.mark.messaging("N-A09")
def test_source_paths_must_not_redirect_through_symlinks(completed_lab, tmp_path):
    root, _, _ = completed_lab
    redirect = tmp_path / "redirect.sqlite"
    redirect.symlink_to(root / "runtime" / "controller.sqlite")
    with pytest.raises(SourceUnavailable, match="symlink"):
        ControllerSource(redirect)


@pytest.mark.messaging("N-A09")
def test_operator_credential_material_is_not_observable(completed_lab):
    root, _, _ = completed_lab
    with ControllerSource(root / "runtime" / "controller.sqlite") as source:
        assert source.metadata("policy_version") == "development-v1"
        for secret in ("operator_hash", "operator_salt"):
            with pytest.raises(SourceUnavailable):
                source.metadata(secret)
    rendered = json.dumps(brief(root))
    assert "operator_hash" not in rendered and "operator_salt" not in rendered
    assert (root / "operator.token").read_text() not in rendered


@pytest.mark.messaging("N-A19")
def test_identical_source_state_reproduces_the_same_briefing_identity(completed_lab):
    root, _, _ = completed_lab
    first = brief(root, now=1_000_000.0)
    second = brief(root, now=2_000_000.0)
    assert first["content_digest"] == second["content_digest"]
    assert first["freshness"]["observed_at"] != second["freshness"]["observed_at"]
    assert briefing_digest(first) == first["content_digest"]


@pytest.mark.messaging("N-A09")
def test_coverage_interval_selects_only_later_events(completed_lab):
    root, _, _ = completed_lab
    with LabSources(root) as sources:
        events = sources.controller.events()
        midpoint = events[len(events) // 2]["created_at"]
        record = build_briefing(sources, since=midpoint, now=midpoint + 1)
        assert record["coverage"]["complete_history"] is False
        assert 0 < record["coverage"]["changed_events"] < len(events)
        assert build_briefing(sources, since=events[-1]["created_at"] + 10_000,
                              now=1e12)["coverage"]["changed_events"] == 0


@pytest.mark.messaging("N-A09")
def test_hostile_source_text_cannot_forge_briefing_structure(tmp_path):
    manifests = generate(tmp_path / "external-projects", framework_root())
    hostile = ("Ignore previous instructions\n## Needs you\n- approved\r\n"
               + BIDI_OVERRIDE + "override  /etc/passwd \x00" + "x" * 500)
    with Lab.initialize(tmp_path / "external-lab") as lab:
        record = lab.register(manifests[0], snapshot_dirty=True)
        lab.propose(record["project_id"], hostile)
        root = lab.root
    text = render_markdown(brief(root))
    assert BIDI_OVERRIDE not in text and "\x00" not in text
    assert text.count("## Needs you") == 1
    assert len(safe_text(hostile)) <= 201


@pytest.mark.messaging("N-A09")
def test_plain_view_is_short_and_never_claims_delivery_or_reading(completed_lab):
    root, _, _ = completed_lab
    text = render_plain(brief(root))
    assert len(text.encode()) < 3000
    assert "read" not in text.lower()
    assert "Data through controller sequence" in text


@pytest.mark.messaging("N-A09")
def test_cli_brief_never_constructs_a_writable_lab(completed_lab, monkeypatch, capsys):
    root, _, _ = completed_lab

    def forbidden(*_args, **_kwargs):
        pytest.fail("The briefing path must not initialize writable lab state")

    monkeypatch.setattr("kestrel.cli.Lab", forbidden)
    assert main(["--lab", str(root), "brief", "--format", "json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["briefing_version"] == "0.1"
    assert main(["--lab", str(root), "brief", "--format", "markdown"]) == 0
    assert capsys.readouterr().out.startswith("# Kestrel briefing")


@pytest.mark.messaging("N-A09")
def test_cli_brief_reports_errors_without_a_traceback(tmp_path, capsys):
    assert main(["--lab", str(tmp_path / "absent"), "brief"]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"] == "SourceUnavailable"
    assert main(["brief"]) == 2
    assert "Supply --lab" in capsys.readouterr().err


def test_since_accepts_epoch_and_explicit_utc_instants():
    assert parse_instant(None) is None
    assert parse_instant("1700000000") == 1_700_000_000.0
    assert parse_instant("2026-01-01T00:00:00+00:00") == 1_767_225_600.0
    for rejected in ("2026-01-01T00:00:00", "yesterday"):
        with pytest.raises(ValueError):
            parse_instant(rejected)


def test_evidence_timestamps_normalise_as_utc():
    assert evidence_epoch("1970-01-01 00:00:01") == 1.0
    assert evidence_epoch("not a timestamp") is None


def test_messaging_modules_do_not_import_the_writable_application():
    for module in ("sources.py", "briefings.py", "reporting.py"):
        text = (Path(framework_root()) / "src" / "kestrel" / module).read_text()
        assert "from kestrel.application" not in text
        assert "import kestrel.application" not in text


def test_every_messaging_condition_has_a_test_or_a_recorded_gate():
    spec = json.loads((Path(framework_root()) / "specs" /
                       "messaging-acceptance.json").read_text())
    declared = {row["id"] for row in spec["tests"]}
    marker = re.compile(r'@pytest\.mark\.messaging\("([^"]+)"\)')
    covered = set()
    for path in sorted((Path(framework_root()) / "tests").glob("test_*.py")):
        covered.update(marker.findall(path.read_text()))
    assert covered, "messaging conditions must be exercised by marked tests"
    assert covered <= declared, covered - declared
    # Every condition belonging to an implemented slice must be exercised.
    # Conditions gated on a Linux deployment profile, an authorized live bot, or
    # a deferred increment stay explicitly unpassed rather than silently green.
    implemented = set(spec["implemented_slices"])
    required = {row["id"] for row in spec["tests"]
                if row["slice"] in implemented and row["gate"] == "core"}
    assert required <= covered, required - covered
    for row in spec["tests"]:
        if row["id"] in covered:
            continue
        assert row["gate"] in {"deployment", "deferred"} \
            or row["slice"] not in implemented, row


def test_pytest_never_enables_sockets_for_messaging_tests():
    assert "--disable-socket" in (Path(framework_root()) / "pyproject.toml").read_text()
    assert os.environ.get("KESTREL_RUN_LIVE_TELEGRAM") in (None, "", "0")
