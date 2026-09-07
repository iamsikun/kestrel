# Build state

Follow-up review and repairs complete on `build/first-pilot`. Tested implementation:
`ac9879e9a7725558468112f9f3950c687cf0a84d` (repairs in `bbc40cd` and `ac9879e`).
Subsequent verification documentation changes no runtime code. Review assessment:
[docs/REVIEW_FOLLOWUP.md](docs/REVIEW_FOLLOWUP.md); decisions:
[docs/DECISIONS.md](docs/DECISIONS.md).

The deterministic offline pilot runs two generated external synthetic projects,
six charged attempts (two offline agents, four scientific workers), zero provider
calls, and two honest valid `not_supported` findings. Execution/evidence boundaries,
ledger state/fences/budgets, protected evaluator, artifacts and CLI exist. The
Docker driver is tested separately; general isolated campaigns are not wired to Lab.

Remaining review defects repaired: valid completion resolves registered evidence
and checks its attribution/labels; reports reject inconsistent legacy outcomes;
Lab registers typed amendment contracts on run/cancel without inheriting approval;
offline recovery binds provider identity/version; memory logs the actual grant reason.
Six selected counterexamples failed before repair. No pinned specification changed.

Exact-revision verification (2026-09-07), commands and hashes in
[docs/VERIFICATION.md](docs/VERIFICATION.md):

- Core unit/integration/adversarial/state-machine: 249 passed, 1 skipped (A28),
  12 separately executed tests deselected; exit 0.
- Offline wheel build and clean install/installed demo: passed; 1 install test, exit 0.
- Actual Docker Linux VM isolation: 11 passed, exit 0; no remaining test containers.
- Claude's unchanged retained audit probes against the source archive: 83 passed, exit 0.
- Ruff, original pack integrity (20 files/46 requirements), diff whitespace: exit 0.
- Release accounting: 42 passed / 4 blocked, exit 1; no integrity issues;
  all composite readiness flags false and deployment.authorized false.

Evidence: `/private/tmp/kestrel-verification-ac9879e/`, including four JUnit records,
install logs, demo, both exported packets, original audit and SHA256 manifest.
Wheel SHA256: `e33177f6c7f4cdb14a4c512ce5564d3843707bea5621a709a79e2625f82eac79`.
Temporary evidence is subject to system cleanup. Prior runs certify prior code only.

Blockers unchanged: A28 actual sanitized provider captures; A30 authorized live
integration; A31 actual credential boundary is unauthorized, unimplemented and
untested; A43 target GPU. VM measurements do not certify a native Linux deployment.
No target service audit, user-namespace remapping or pinned seccomp/AppArmor profile
is claimed. Finite-reader timing and writable-workspace limits are advisory.

Next unblocked action for handoff: independently review `bbc40cd` and `ac9879e`,
starting with `docs/REVIEW_FOLLOWUP.md`, and verify the retained evidence hashes.
This repair author cannot provide the sole independent deployment review. A28 can
next run offline when provenance-bearing public-synthetic captures are supplied;
A30/A31/GPU work needs the stated authorization/resources. No live calls, pushes,
deployment, host-service changes or research repository access occurred.
