# Build state

Functional deterministic pilot on `build/first-pilot`, with local commits through
`949c82020dcbe8b5c6b763d7085b9d26401f2162` and a tested final cancellation repair.
Not release-certified. No deployment, live model calls, private project access,
pushes, cloud services, or host-service changes have been performed.

## What exists

Strict contracts and recipe identities; generated external numerical/counterexample
Git fixtures; nonexecuting complete source snapshots and independent candidates;
SQLite authority, budgets, attempts, leases, reconciliation and history; bounded
artifact ingestion, lineage, invalidation, retention and export/import; independent
finite-domain evaluation and honest negative findings; mock/replay adapters;
trusted-fixture development execution and an actual tested Docker backend; CLI;
clean-install and machine-readable acceptance reporting.

Final coverage repairs add approved, budgeted offline agent tasks with durable
failures, active fixture conformance, and actual independent progress beside a
blocked branch. A fixture campaign now explicitly accounts for one agent attempt
and two scientific trials; proposal creation runs no agent. Live entry points
remain unavailable. See `docs/USAGE.md`, `docs/DECISIONS.md`, and `docs/PROVIDER_STATUS.md`.

## Verified evidence

Exact `949c820` complete core run: 223 passed, 1 skipped (A28), 12 deselected,
16.62s, exit 0; `/private/tmp/kestrel-verification-949c820/core.xml`.
Subsequent static review found that an invalidated/malformed offline agent plan
prevented cancellation of a reserved attempt. The reservation remains held
(fail-closed), but the known-stopped reader needs a historical cancellation path.
The repair passed 43 focused tests, 1 skipped (A28), exit 0, 1.17s:
`/private/tmp/kestrel-agent-cancellation-repair.xml`. It covers malformed,
invalidated, deleted, tampered and restricted plans, cancellation interruption,
and wrong-backend stop-proof rejection. Final suites must test the new commit.

Exact `a75fb83` evidence: `/private/tmp/kestrel-verification-a75fb83/`.
Serial commands exited 0: core suite 192 passed, 1 skipped, 12 deselected;
`uv build --offline`; fresh external offline wheel install/demo 1 passed;
Docker Linux VM suite 11 passed, 19 deselected. Ruff and specification integrity
passed. The installed demo ran two campaigns/four scientific attempts, differences
4 and 2, both `not_supported`, zero provider calls. These artifacts predate the
current coverage repairs and must not certify them.

Current working-tree checks: source-registration tests 25 passed (exit 0);
application/adversarial/conformance tests 34 passed in 11.57s (exit 0), recorded in
`/private/tmp/kestrel-coverage-integration.xml`. Agent failure/recovery/reader tests
36 passed, 1 skipped (A28), exit 0, `/private/tmp/kestrel-agent-execution.xml`.
Ruff and `git diff --check` pass. Source A05/A08 tests now have explicit mappings.

Independent audit of `f5bc705` found a cancelled-before-dispatch launch race and
shared-content lineage mixing. Committed `a75fb83` repairs passed regressions;
an independent static-only recheck found both mechanisms addressed. Original
reports: `/private/tmp/kestrel-independent-review-fxaOwH/audit/REPORT.md` and
`/private/tmp/kestrel-independent-recheck-mhVoWd/RECHECK.md`. Neither is a target
Linux deployment audit, nor an audit of subsequent new integration code.

## Limits and next action

A28 actual sanitized Codex/Claude captures are unavailable; documentation-based
synthetic events do not satisfy that core gate. A30/A31 live/credential integration
is unauthorized and untested; A43 target GPU is untested. No skipped gate passes.
Docker Desktop Linux VM measurements do not establish separately operated target
Linux controller/credential authority. Hard writable-workspace storage quotas are
unsupported and requested hard caps are rejected. Developer CLI execution is
restricted to exact synthetic fixtures; general isolated project execution is
not enabled. Finite parser elapsed checks and developer subprocesses are not
adversarial security boundaries.

Exact next action: commit the verified cancellation repair, then serially run complete core,
rebuilt clean install and actual container suites
for that exact revision. Regenerate acceptance evidence and record its hashes and
remaining operator gates. Preserve all 20 supplied specification files unchanged.
