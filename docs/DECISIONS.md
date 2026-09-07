# Implementation decisions

## 2026-09-06 — first pilot

- Preserve the supplied specification files and their integrity manifest unchanged.
  Implementation instructions and results live in new documents.
- Use Python >=3.12 with strict Pydantic v2 contracts, SQLite, argparse, and immutable
  JSON/file artifacts. CLI needs no additional framework. Pin resolved dependencies
  after smoke tests; provider libraries remain absent until a working authorized test.
- Development execution accepts only exact framework-generated fixture sources. It
  provides no adversarial isolation. Arbitrary project or agent-produced code requires
  an actually measured isolated backend; an unavailable profile fails closed.
- Fixtures are deterministic numerical prediction and integer counterexample search.
  Comparison is exhaustive over a finite synthetic domain; no population statistical
  inference or general scientific validity is claimed. A deliberately inferior
  treatment must complete with `not_supported`.
- Operator tokens and databases in developer labs demonstrate controller policy logic,
  not a deployed service identity boundary. No deployment endpoint or self-upgrade
  operation is exposed. Source editing does not update a deployed controller.
- Check official Pydantic strict-mode, Hypothesis stateful testing and uv layout docs
  before use. References: https://docs.pydantic.dev/latest/concepts/strict_mode/,
  https://hypothesis.readthedocs.io/en/latest/stateful.html,
  https://docs.astral.sh/uv/concepts/projects/layout/ (checked 2026-09-06).

## Evidence and authority refinements

- Explicit directory snapshots are the supported source-resolution mode. They
  account for tracked, ignored and untracked files plus empty directories. Git
  administrative data is recorded as excluded; clean Git revision resolution and
  unresolved submodules/LFS are rejected rather than guessed.
- Raw content identity and execution occurrence are different. Identical output
  bytes have no campaign-specific lineage; unique execution receipts and
  observations supply producer/attempt/contract provenance. Comparisons include
  campaign and observation identities. This prevents deduplicated content from
  joining unrelated campaigns during export. Low-level content-store metadata is
  global; adding conflicting lineage to identical content is rejected. Callers
  must put context-dependent meaning in distinct occurrence records. A derivative
  of restricted data cannot downgrade its classification through a lineage edge.
- An export starts from one campaign packet and traverses only its references.
  Historical export records may reference invalid evidence, but inherit a stale
  status and cannot acquire independent scientific assurance. Imported artifacts
  stay imported even when controller history is restored alongside them.
- Failure diagnostics occupy a separate immutable controller table, not the
  successful result collection. Execution receipts retain actual stdout/stderr,
  return code, stop confirmation and recipe identity for valid and failed attempts.
- A development authorization token demonstrates policy checks only. Actual live
  provider use, arbitrary generated-code autonomy and deployed controller upgrades
  are unavailable. Official provider documentation informs offline raw-record
  readers; synthetic examples are not claimed as real provider captures.
- Docker uses a pinned existing public image, unprivileged isolated namespaces,
  read-only root, bounded tmpfs and explicit CPU/RAM/PID caps. A detached host
  supervisor enforces deadlines against a worker that stops its own timer. Writable
  bind storage is advisory; hard-quota requests fail closed. A responsive daemon
  and external supervisor remain assumptions, reported separately from measurements.
- Runtime test suites must run sequentially across independent lab instances.
  Initial parallel component verification is not evidence of a global host-wide
  resource ceiling. Final release evidence is collected serially. Per-controller
  slot reservations cannot constrain independently started controllers or other apps.
