# Build state

Existing-project integration implemented and verified on `feat/project-integration`.
Worktree: `/private/tmp/kestrel-project-integration`, based on committed `3c47dc3`.
Verified runtime revision: `2805e3b8cfb77a202ad8c874454241892542ea06`. Later build-state documentation
changes no runtime code. The original checkout was never switched or edited by this
work; its independently advancing reporting work remains preserved.

Implemented: strict `.kestrel/project.yaml`, exclusive scaffolding, stable connections
and immutable contract revisions; selected-source preview/expected-digest snapshots;
parameter schemas and bounded source replacements; local Python client and CLI;
worker-only helper and two ordinary synthetic project examples; execution-only
contracts on the existing approval/budget/attempt/fencing ledger; actual Docker
execution, recovery, cancellation and attributable evidence export. Cancellation
remains available after input invalidation, while launch is refused.
Legacy complete-directory snapshots and fixture commands remain supported.

Installation/workflow: [README](README.md), [tutorial](docs/PROJECT_TUTORIAL.md),
[source semantics](docs/PROJECT_INTEGRATION.md). Original README bytes are archived
at `docs/specification/README.md`: 4034 bytes, SHA256
`2aab712ff1d155cc0b45ccfece118ec76295c291562f84913faffc57e11a1037`.
The inventory diff changes only that path. Original validator bytes are unchanged;
`tools/validate.py` distinguishes kit integrity from runtime verification.

Final serial verification (2026-09-07), every command exit 0:

- Ruff: `ruff check src tests tools/release_gates.py tools/prepare_wheelhouse.py tools/validate.py`.
- Specification: `python tools/validate.py`, 20 files and all 46 original requirements.
- Whitespace: `git diff --check`.
- Core/state-machine/adversarial: `python -m pytest -m 'not isolation and not install' -q`:
  **296 passed, 1 skipped (A28 actual provider captures), 18 deselected**.
- Actual Docker VM: `KESTREL_RUN_ISOLATION=1 python -m pytest -m isolation -q`:
  **17 passed, 298 deselected**. Includes existing egress/filesystem/resource tests
  plus two-project experiments, malformed outputs, traversal, timeout, cancellation
  after invalidation, and reopened-controller interrupted-run recovery.
- Wheel: `uv build --offline`; no new dependencies or implicit image pull.
- Clean installation: `python -m pytest tests/test_install.py -q`: **1 passed**.
  Empty temporary environment, offline dependency wheelhouse, installed fixture demo,
  installed onboarding/proposals, both actual Docker adapter executions with parameters
  and replacements, unchanged source assertions, export, and stopped-container cleanup.

Full argv, exit statuses, source revision, logs and hashes are in
`/private/tmp/kestrel-integration-evidence/checks.json`, `verify.py`, and `hashes.json`.
That script records the exact environment: PYTHONPATH points to this worktree;
Docker uses existing public `ghcr.io/astral-sh/uv@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca`;
clean installation uses `/private/tmp/kestrel-wheelhouse`. Python/Ruff come from
the pre-existing development venv, without reinstalling the user's tool.
JUnit scope/profile metadata retain default `core`/`development`; the commands and
actual Docker tests document the measured VM execution. These receipts are not a
native Linux deployment or release-readiness certification.

Evidence SHA256:
- `core-2805.xml`: `8010e9e65c742c9f53365f1fd54291b0b70c78fcd6dcff7d3f6aa4c40576c1ef`
- `isolation-2805.xml`: `b50363860a9d51ead9af7cf80802f008161342d8ad58a0eb580e39c1a6a9426d`
- `install-2805.xml`: `6c75cecdf36084327257eabb12d63e4b5d055b12be0cc8d684a8410f7b88c837`
- `install-final.json`: `144b692edc9cced149a8547d00be8053e60f081f72977abf36f798d962d95cd1`
- Wheel: `27c8ca7a205ad01f63e265b24d3210b06e8d36e5e94f2a3b0fa6639025f3054e`

Earlier failures retained honestly: initial sandbox core check was interrupted
(exit 2, 5 failures/48 passes) because process inspection could not confirm fixture
termination. Approved-access reruns passed. Initial new Docker test exposed job-ID
mapping/pending-start handling; repaired, all later gates passed, and its leftover
confirmed-stopped container was removed. Initial uv build needed approved cache
access. No unavailable or skipped gate is counted as passing.

Remaining gates: A28 actual sanitized provider captures; A30 authorized live
integration; A31 credential boundary; A43 target GPU; independent target deployment
review and native Linux isolation verification. No live provider, paid service,
host configuration, user-data campaign, push, publication, current-tool reinstall,
`llm-lab` enrollment or deployment occurred. The tests use synthetic public data
and network-free workers. Writable workspace storage remains advisory; hard quota
requests fail before launch. Project outputs remain self-reported and scientific
outcome is explicitly not evaluated.

Implementation task complete. Exact next unblocked action: independently review
this branch and verify the evidence hashes before any separately authorized merge
or deployment. Temporary worktree/evidence paths are subject to OS cleanup; commits
are retained in the original repository. Prior build history remains in Git.
