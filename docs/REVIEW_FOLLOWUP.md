# Follow-up to Claude's audit repairs

Reviewed input: auditor-authored code `7db8a6c`, documentation HEAD `37272b7`.
Original audit report SHA256:
`b8ab1bbd4a9d015b4de1590493bab9d82bce38df6a06c1e18bcd13e604ca4247`.
Repairs from this review: `bbc40cd` and `ac9879e`. No specification or acceptance
requirement was edited. See `VERIFICATION.md` for exact final execution records.

## Findings and disposition

| Area | Assessment and repair |
|---|---|
| F1 completion evidence | Still incomplete in Claude's repair: SHA256 syntax did not establish registration. A real completed fixture's outcome could cite a nonexistent hash and remain valid. Valid completion now requires an attached application-owned artifact store and resolves, hash-checks and checks status/attribution of its evidence before the SQLite outcome commit. A detached ledger fails closed for valid outcomes. |
| F1a labels | Campaign attribution alone did not prevent a positive ledger label from citing that campaign's real negative comparison. Both completion and reports now require evidence agreement with execution, protocol and finding labels. Legacy/inconsistent reports become invalid/inconclusive and cannot support quantitative claims. |
| Amendment registration | Reproduced: direct `Controller.amend` followed by Lab cancellation failed because the child contract had no artifact. Lab now shares typed contract registration across proposal, execution and cancellation. New tasks and a fresh approval remain explicit. Tests exercise cancellation/export and real amended execution, rejecting the parent approval. |
| F3 provenance | Source labels and non-mock version checks still allowed a mock receipt to name an arbitrary provider/version. Recovery now binds provider and version to the built-in mock, selected wire backend, or frozen normalized recording. Added mock and normalized-replay identity regressions. |
| F2 logging | Access decisions were correct, but same-project approved-public retrieval without a project grant was logged as using a source-project permission. The reason now identifies the public-sharing authorization actually used. No access was expanded. |

Six selected counterexample tests failed before repair: unresolved evidence,
opposite finding, inconsistent legacy report, amendment cancellation, detached
completion and mock-version forgery. The regression source is committed; the
original focused invocation exited 1. Focused repaired suites passed 103 and then
78 cases; final full-suite evidence supersedes those working-tree runs.

## Assessment of the handoff questions

1. **Controller/application coupling:** resolved at the boundary that owns artifact
   registration. Raw controller amendments remain ledger operations; using a typed
   child through Lab registers its contract from authoritative persisted content.
   Existing artifacts and their trust labels are not rewritten. Arbitrary generic
   controller dictionaries do not become executable Lab campaigns.
2. **Substantive finding implies succeeded execution:** retained for this pilot's
   frozen `evaluate_each_once` workflow. The real numerical/counterexample negative
   results still complete successfully. This is an explicit pilot completion
   policy, not a general scientific theorem equating execution and finding axes.
3. **Valid/inconclusive without a succeeded attempt:** the pilot does not implement
   a zero-observation scientific confirmation path. Such work remains incomplete
   or invalid; importing a document does not certify a protocol. A new regression
   explicitly permits failed aggregate execution with valid protocol/inconclusive
   finding after verified partial work, so the axes have not been collapsed.
4. **Memory context restriction:** retain it. SCIENCE section 7 defaults cross-project
   sharing to approved de-identified methodology/claims. A source grant is not
   sufficient to carry restricted data into a different context. This is stricter
   than A45's minimum, as already documented, and introduces no new permission.
5. **Recognized `live` enum label:** retain recognition and explicit rejection at
   the shared offline validator. Parsing the label does not authorize execution.
   Removing it from the raw model is unnecessary to close the current path; a
   future live integration needs its own separately reviewed policy and tests.
6. **Report compatibility:** `evidence[].attributable` is retained;
   `evidence[].consistent` is added. Exported report bytes/hashes change. Existing
   bundles remain hash-verified and retain imported assurance. Corrupt bytes still
   raise a hash error before attribution/consistency checks.
7. **Test edits:** Claude's `succeeded()` helper added a shape-valid simulated
   observation reference, not a registered artifact. Its state-machine intent was
   preserved. The successful-completion unit test now explicitly supplies an
   actual artifact store and a model outcome record. Real scientific validation
   remains in the separate fixture/evaluator integration tests. The failed-work
   tests still reach their original state checks before evidence resolution; no
   rejection assertion or original acceptance invariant was removed.

## Scope and remaining gates

Claude's five retained test files were copied byte-for-byte into an external
review directory and rerun against an archive of the repaired source. Their hashes
are retained. A copied pytest provenance helper was added; no audit test assertion
was edited. These are reruns of existing probes, not newly authored independent
proofs. All repositories/sentinels were synthetic and external.

This review did not alter the container driver or deployed configuration. VM-only
isolation, absence of user-namespace remapping and pinned seccomp/AppArmor profiles,
unwired general isolated campaign execution, and trusted-host/SQLite/JUnit limits
remain explicit. Valid completion resolution is a check against controller mistakes;
a malicious trusted controller can still write its store or assign assurance. No
claim of protection from a hostile host administrator is made.

A28 still needs actual sanitized Codex/Claude captures. A30/A31 remain unauthorized
and untested; A31 has no implemented live credential boundary or integration test.
A43 still needs target GPU hardware. No deployment is authorized. The follow-up
fixes have an author and need a separate current-revision review before deployment;
their author cannot supply the sole independent deployment review.
