# Build state

Status: functional deterministic pilot on `build/first-pilot`; under verification,
not release-certified. No deployment authorized.

Verified starting facts (2026-09-06): specification-only baseline `651106c`;
`python3 tools/validate_pack.py` exit 0 (20 files, 46 acceptance requirements).
Host is macOS arm64, Python 3.13.9, uv 0.9.3. Read-only Docker version probe
returned Desktop 4.88.1 / Engine 29.7.2, Linux arm64. This is not an isolation pass.

Implemented: strict contracts/recipe hashes; two generated external Git repositories;
complete explicit source snapshots; safe artifact ingestion; SQLite approvals, attempts,
leases, reservations and history; independent finite-domain evaluators; mock/replay
agents; development and Docker drivers; negative findings; recovery; invalidation;
retention/backup; CLI and campaign-scoped evidence export/import; release-gate reporter.

Evidence so far (commands exit 0 unless explicitly stated):
- `.venv/bin/python -m pytest -m 'not isolation and not install' --junitxml=/private/tmp/kestrel-core-precommit-fixed.xml -q`: 177 passed, 12 deselected.
- `.venv/bin/python -m pytest tests/test_application.py tests/test_application_adversarial.py -q`: 23 passed after export/provenance repairs.
- `uv run --offline python -m kestrel demo --offline --output /private/tmp/kestrel-first-demo-verified`: two COMPLETE campaigns, four attempts, negative findings; numerical error difference 4, counterexample count difference 2; zero provider calls. Early demo under tool sandbox returned UNKNOWN because process inspection was denied; reservations were retained, not certified.
- Docker tests: 11 actual Linux VM tests passed, 13 core cases deselected, 19.23s; `/private/tmp/kestrel-isolation-final.xml` SHA256 `c7bac2d1abd71038f1e0ba63950721844401e29f0d113d45d99a61cb78e89d53`. Source then uncommitted; rerun for committed revision.
- Clean external wheel install and demo: 1 passed using hash-verified wheelhouse `/private/tmp/kestrel-wheelhouse`; `/private/tmp/kestrel-package-install-evidence.json`. Final rebuild/retest required after remaining integration edits.
- `.venv/bin/ruff check src tests tools/release_gates.py`: passed.

Review caught and repairs address cross-campaign export leakage, CLI validation drift,
hash-only claim certification, state-path symlinks, stale-history export, and imported
assurance laundering. Failure diagnostics are persisted separately from valid results.
Regression additions and final exact-revision verification are next. Assisted review:
`/private/tmp/kestrel-review-precommit.md`; this is not an independent deployment audit.

Blocked gates: A28 actual provider captures unavailable (synthetic documented wire
fixtures only); A30/A31 live calls and credential integration lack explicit authorization;
A43 target GPU not tested. Docker Desktop Linux VM measurements do not authorize or
complete deployment to a separately operated Linux target. Hard writable-workspace
storage quotas are unsupported and requests for them are rejected. No skipped gate
counts as passing. General external code is registered but not executed by developer CLI.

Exact next action: run the added provenance regression, commit the tested implementation,
then rebuild/install and rerun core/isolation gates with that exact revision; obtain a
separate read-only code review and generate the acceptance report from actual JUnit XML.
