# Build state

Status: functional deterministic first pilot on `build/first-pilot`. Tested code:
`7db8a6cb81c6805373889424c23151b58b63997b`, which adds independent audit repairs to
completion authority, evidence attribution, memory scope and provider labelling.
Acceptance is incomplete: 42 requirements passed, 4 blocked. No deployment authorized.
This revision was patched by its own independent auditor, so it has no independent
review; the completion, evidence-attribution and provider-labelling repairs are
trust-boundary changes and require a further independent review by a third party before
any deployment. No ongoing jobs or promised continuation after this session.

## Implemented

Strict typed contracts/recipe identities; generated external numerical and
counterexample Git fixtures; nonexecuting complete snapshots and independent
candidates; SQLite authority, budgets, attempts, leases, reconciliation and history;
bounded immutable artifacts, lineage/invalidation/retention/export/import;
independent finite-domain evaluation and honest negative findings; mock/replay
agents using the approved TaskSpec/Attempt ledger; development and Docker drivers;
active fixture conformance; CLI; clean-install and acceptance-report tooling.

Each approved fixture campaign accounts for one offline agent task and two
scientific trials (three attempts, eleven reserved seconds, zero model calls/tokens).
Proposals alone grant no execution. Agent failures have durable diagnostics and
conservative recovery. Cancellation can confirm a stopped reader despite malformed
or unavailable plans without claiming the plan contents are verified.

## Verified on 2026-09-07 local / 2026-09-07 UTC

Final evidence: `/private/tmp/kestrel-verification-audit-fixes/`.
Exact commands, platform versions and hashes: `docs/VERIFICATION.md`.
Records for `2157914` remain historical and do not certify this source.

- Core: `.venv/bin/python -m pytest -m 'not isolation and not install' -q`, exact-revision JUnit `core.xml`: 238 passed, 1 skipped (A28), 12 deselected, 16.33s, exit 0. The eight added tests are audit regressions for the repairs below.
- Build: `uv build --offline`, exit 0. Wheel SHA256 `65759b17ee1025faaff2407784f3a17927236ab70cf03cc52ac70950f45c41d9`.
- Fresh external offline install: `.venv/bin/python -m pytest tests/test_install.py -q`, `install.xml` and `install-evidence.json`: 1 passed, 2.41s, exit 0; empty cache, verified local wheelhouse, no index/builds/downloads.
- Actual Linux VM isolation: `.venv/bin/python -m pytest tests/test_runners.py -m isolation -q`, `isolation.xml`: 11 passed, 19 deselected, 17.64s, exit 0. Existing exact image only; all test containers removed.
- Ruff, `git diff --check`, and `python3 tools/validate_pack.py` passed (exit 0). All 20 supplied specification files and 46 requirements are unchanged. Runtime suites ran serially.
- Demo: two COMPLETE campaigns, six attempts (two agents/four workers), verified differences 4 and 2, both `not_supported`, zero provider calls, both evidence entries attributable to their own campaign. `demo.json` and `evidence-packet.zip` retained externally.
- `tools/release_gates.py` from final XML records: exit 1 as required for incomplete acceptance. Core 36 passed/A28 blocked; isolation 6 passed; live A30/A31 and GPU A43 blocked; no missing tests or provenance issues. Report SHA256 `37ad7b7e9bd2208e2f10a1351da61ced6574de8364beb33f59dfaa3b9718f6ef`. Full hashes in `sha256.json`.

Independent audit of `f5bc705` found a late-dispatch cancellation race and mixed
content lineage; `a75fb83` repaired both with regressions and independent static
recheck. A second independent audit of `e03b73f` reproduced every build claim and the
release-gate accounting, then found three API-reachable defects: completion could
certify a substantive finding with no successful work and unresolvable evidence
references; a report could present another campaign's recomputed analysis as its own
certified evidence; memory retrieval ignored the asking project; and the shared result
boundary accepted a live provider label. `7db8a6c` repairs all four with regressions
mapped to A27, A29, A40 and A45. That auditor authored the repairs and is therefore not
an independent review of them. Earlier revision records remain historical and are not
used to certify current source.

## Blockers and exact next action

A28 requires actual sanitized public-synthetic Codex/Claude captures; synthetic
wire fixtures are not a pass. A30/A31 require explicitly authorized live provider
and credential integration; no provider version/model has a working live test.
A43 requires actual target GPU testing. Docker Desktop Linux VM measurements do
not establish or authorize a separately operated target Linux controller.

Developer CLI executes exact synthetic fixtures only. General isolated campaign
execution is not enabled. Hard writable-workspace quotas are unsupported and
requests fail closed; the watchdog is advisory. Finite parser elapsed checks and
plain subprocesses are not adversarial security boundaries. The daemon/supervisor
must remain responsive; uncertainty retains capacity. No host-wide resource cap
or general scientific correctness is claimed.

A further independent review of `7db8a6c` by a reviewer who did not write it is
required before deployment, covering the completion invariants, report attribution and
provider labelling.

Next action requiring new input: supply provenance-bearing sanitized public
Codex/Claude captures in the format checked by `tests/test_provider_records.py`,
then run that gate offline with `KESTREL_PUBLIC_PROVIDER_CAPTURES` and regenerate
revision-bound evidence. Generating new live captures requires explicit authority.
Before deployment, a separate operator must review this wheel/current source and
the target Linux service, credential and mount boundaries, then authorize it.
No push, publication, private research access, live call or host-service change
has been performed. Runnable setup/demo and conformance commands: `docs/USAGE.md`.
