# Build state

Review follow-up in progress on `build/first-pilot`, starting from `37272b7` /
auditor-authored code `7db8a6c`. Read Claude's original report at
`/private/tmp/kestrel-verification-audit-fixes/audit-REPORT.md` and the repair diff.
No deployment or external-data/provider permission is granted by this review.

New regressions reproduced remaining defects before repair: unresolvable evidence
could complete a valid campaign, a positive ledger label could cite its own negative
comparison, detached controller completion skipped resolution, an amendment could
not be cancelled/exported due to missing contract registration, and mock receipt
recovery accepted an arbitrary provider version. Six selected regression cases
failed before repair (exit 1, expected). Provider-identity variants also added.

Repairs now resolve valid completion evidence through the attached store, verify
outcome labels in completion and reports, register typed controller amendments on
Lab execution/cancellation, and bind offline receipt provider identity/version.
They retain Claude's memory-context policy and explicit rejection of live labels.
No original acceptance/specification file changed. Decisions: `docs/DECISIONS.md`.

Working-tree evidence: focused application/controller/agent suite 103 passed,
exit 0, `/private/tmp/kestrel-followup-focused.xml`; strengthened amendment,
partial-outcome and provenance suite 78 passed, exit 0,
`/private/tmp/kestrel-followup-focused-final.xml`. Static and final exact-revision
verification are next. Prior 238/core, 1/install, 11/VM isolation results at
`/private/tmp/kestrel-verification-audit-fixes/` certify the previous code only.

Next unblocked action: finish static review, commit the repairs; serially run core,
clean install, actual Docker isolation and the retained external audit probes;
generate a new revision-bound release report and hashes. Record any failure as a
failure, not a skip/pass. Update `docs/VERIFICATION.md` and this state afterward.

Unchanged blockers: A28 actual sanitized provider captures; A30/A31 authorized live
provider and credential-boundary integration; A43 target GPU. Isolation is measured
only in a Docker Desktop Linux VM. No isolated campaign application, target Linux
service/credential audit, user-namespace remapping or pinned seccomp/AppArmor profile
is claimed. The finite reader timer and writable-workspace watchdog are advisory.
No pushes, deployment, host-service changes or research repository access performed.
