# Build state

Existing-project integration implementation on `feat/project-integration`, separate
worktree `/private/tmp/kestrel-project-integration`, based on `3c47dc3`. Original
checkout and its reporting revision remain untouched. No user repositories read,
no current tool reinstall, no live calls, pushes, deployment or new image pulls.

Implemented: strict project contract, exclusive scaffolding, stable connections
and immutable revisions, selected-source preview/expected-digest snapshots,
parameter and bounded replacement validation, local Python client/CLI, worker-only
command helper, two external synthetic examples, execution-only controller-backed
Docker lifecycle and evidence export. Legacy full snapshots remain intact.
Original README archived byte-for-byte; only its inventory path changed. Original
validator also remains byte-for-byte; `tools/validate.py` adds runtime-not-run output.

Verification in progress. Evidence directory:
`/private/tmp/kestrel-integration-evidence/`.
- Initial onboarding suite: 20 passed, exit 0; expanded suite: 21 passed, exit 0.
- Actual Docker VM suite: 17 passed, exit 0 (`isolation.xml`), before the final
  reporting compatibility and source-evidence index changes; final rerun pending.
- Core before those changes: 294 passed, 1 skipped (A28), 18 deselected, exit 0
  (`core-verified.xml`). Initial sandbox run interrupted after 5 failures/48 passes,
  exit 2 (`core.xml`): fixture termination could not be confirmed without process
  inspection. Rerun with approved access passed.
- Ruff and original specification integrity: exit 0, 20 files/46 requirements.
- Initial wheel build: exit 0, but final wheel/install verification pending.

Exact next action: final serial core, Docker isolation, offline wheel build and
clean-wheel installation gates; record hashes and results here, commit evidence
summary. Additional live/provider/GPU/native-Linux deployment gates remain unmet;
Docker Desktop measurements do not certify unattended native Linux deployment.
