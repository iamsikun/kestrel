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

## Final coverage review refinements

- The deterministic agent is approved, reserved and recorded through the same
  TaskSpec/Attempt ledger as execution. Proposal creation itself freezes the known
  fixture template without running an agent. An offline agent task must confirm
  that template before its two dependent scientific trials can run. The campaign
  declares three attempts and eleven reserved runtime seconds, with zero provider
  calls/tokens. Agent work is not an evaluated scientific trial or replicate.
- Mock/replay failures and malformed outputs require durable diagnostics and
  conservative recovery. Parser-only failure tests do not establish recorded
  operational outcomes. Recorded provider bytes remain untrusted data; this path
  cannot invoke arbitrary provider callbacks, project imports, tools or live calls.
- Active project conformance uses an explicitly approved existing fixture campaign
  and its bounded execution/evidence path. Static sidecar validation remains
  available before approval. Arbitrary project execution is still unavailable
  through the developer CLI; conformance cannot bypass that restriction.
- A blocked branch leaves the campaign pending while independently ready work
  finishes. Resuming it preserves the completed attempt and existing approval;
  missing work does not become a failed scientific finding.
- Cancelling a finite offline reader must remain possible when its plan is
  malformed, invalidated or unavailable. The controller confirms the reader lock
  is held exclusively and records a separate cancellation occurrence using frozen
  claimed identities and available classification metadata. It does not parse
  the plan or claim its contents were verified. That operational stop record does
  not restore scientific validity or make missing evidence exportable.

## Independent audit repairs

Authored by the independent auditor of `e03b73f` after that read-only review, so
this revision has an author who cannot also be its independent review.

- A substantive scientific finding requires successful execution. Completion now
  refuses `supported_in_scope` and `not_supported` whenever execution failed, was
  lost or was cancelled; such work stays inconclusive. Execution failure is an
  execution outcome, not a negative result, and an unfavourable score is still a
  successful campaign, so an honest negative finding after successful execution
  remains reachable exactly as before.
- Outcome evidence references are content addresses, not free text, and a valid
  protocol requires at least one verified succeeded attempt. The controller holds
  no artifact store handle by design, so it enforces reference shape and the
  existence of verified work; it cannot resolve artifact content.
- Cited evidence must be attributable to the campaign citing it. Campaign outcome
  artifacts carry their campaign identity and a lineage edge to the frozen
  contract, and a report treats unattributable evidence exactly like invalid
  evidence. Valid, independently recomputed bytes produced by another campaign
  confer no assurance here; hashing content and binding it to a verified attempt
  is what makes it evidence.
- Memory retrieval authorizes against the asking project as well as the source
  project. A grant on a record's own project does not follow its principal into an
  unrelated campaign, and approved public de-identified records remain the only
  cross-project channel, so restricted material cannot travel that way. This is
  stricter than the acceptance text, which requires source permissions and
  classification checks alone; the requesting project was previously recorded
  without being consulted.
- A result labelled as a live provider call is refused at the shared typed
  boundary, and a replayed durable receipt must still match its frozen plan's
  source and provider version. No authorized live integration, credential
  boundary or version pin exists, so nothing offline may claim live provenance.
  Enabling it requires the separate authorized work behind A30 and A31.
