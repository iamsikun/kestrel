# Build state

Status: functional deterministic first pilot on `build/first-pilot`. Tested code:
`2157914e776b42112e00ec3a760eec7fc024022a`. Subsequent documentation commits do not
change this code. Acceptance is incomplete: 42 requirements passed, 4 blocked.
No deployment authorized. No ongoing jobs or promised continuation after this session.

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

## Verified on 2026-09-06 local / 2026-09-07 UTC

Final evidence: `/private/tmp/kestrel-verification-2157914/`.
Exact commands, platform versions and hashes: `docs/VERIFICATION.md`.

- Core: `.venv/bin/python -m pytest -m 'not isolation and not install' -q`, exact-revision JUnit `core.xml`: 230 passed, 1 skipped (A28), 12 deselected, 16.04s, exit 0.
- Build: `uv build --offline`, exit 0. Wheel SHA256 `212f7f1bb9a00f9148332654f2de3d4a06f620d1aa3b1f065d29a0e24b57f0ab`.
- Fresh external offline install: `.venv/bin/python -m pytest tests/test_install.py -q`, `install.xml` and `install-evidence.json`: 1 passed, 4.00s, exit 0; empty cache, verified local wheelhouse, no index/builds/downloads.
- Actual Linux VM isolation: `.venv/bin/python -m pytest tests/test_runners.py -m isolation -q`, `isolation.xml`: 11 passed, 19 deselected, 18.66s, exit 0. Existing exact image only; all test containers removed.
- Ruff, `git diff --check`, and `python3 tools/validate_pack.py` passed (exit 0). All 20 supplied specification files and 46 requirements are unchanged. Runtime suites ran serially.
- Installed demo: two COMPLETE campaigns, six attempts (two agents/four workers), verified differences 4 and 2, both `not_supported`, zero provider calls. `demo.json`, `numerical-evidence.zip`, `counterexample-evidence.zip` retained externally.
- `tools/release_gates.py` from final XML records: exit 1 as required for incomplete acceptance. Core 36 passed/A28 blocked; isolation 6 passed; live A30/A31 and GPU A43 blocked; no missing tests or provenance issues. Report SHA256 `d938f3baa954da308dcdbc03a2adc6964d939b6f1540957855bc6b000f71562d`. Full hashes in `sha256.json`.

Independent audit of `f5bc705` found a late-dispatch cancellation race and mixed
content lineage; `a75fb83` repaired both with regressions and independent static
recheck. Later coverage review drove tracked agent attempts, active conformance,
actual blocked-branch progress and invalid-plan cancellation. Final tests cover
these additions; no new independent deployment audit is claimed. Earlier revision
records remain historical and are not used to certify current source.

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

Next action requiring new input: supply provenance-bearing sanitized public
Codex/Claude captures in the format checked by `tests/test_provider_records.py`,
then run that gate offline with `KESTREL_PUBLIC_PROVIDER_CAPTURES` and regenerate
revision-bound evidence. Generating new live captures requires explicit authority.
Before deployment, a separate operator must review this wheel/current source and
the target Linux service, credential and mount boundaries, then authorize it.
No push, publication, private research access, live call or host-service change
has been performed. Runnable setup/demo and conformance commands: `docs/USAGE.md`.
