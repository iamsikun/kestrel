# Implementation decisions

## 2026-09-07 — make the root README a current user entry point

- The user's request for usable README instructions supersedes the earlier choice
  to keep the original kit introduction at the repository root. Archive its exact
  bytes at `docs/specification/README.md` and change only that entry's path in
  `kit-manifest.json`, retaining its original size and SHA-256. The validator still
  checks all 20 original payloads; acceptance requirements, milestones, security
  rules, and validator behavior remain unchanged. This is a documented relocation,
  not a new checksum certifying an edited specification.
- The root README becomes maintained implementation documentation: the offline
  demo, how to inspect/export results, and the explicit absence of general research
  project execution or a live agent. No runtime capability or authority is added.

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

## Review of the auditor-authored repairs (2026-09-07)

- Retain the pilot's rule that substantive findings require succeeded execution.
  It applies to the frozen finite `evaluate_each_once` workflow, not a general
  theorem about all research. Invalid/incomplete or zero-observation work cannot
  certify a claim. Failed execution with a valid protocol and an inconclusive
  finding remains possible after verified partial work; a regression covers it.
- Supersede the earlier decision to check evidence shape alone at completion.
  `Controller` optionally receives the application-owned `Artifacts` store; valid
  completion now requires that store and resolves/hash-checks each cited artifact,
  its status, campaign attribution and agreement with all three outcome labels.
  Substantive findings additionally require independent evidence assurance.
  A detached ledger may still retain incomplete/invalid operational history; it
  cannot certify a valid protocol. The controller does not execute project code or
  accept a worker-controlled evidence-resolver callback. Artifact-store lifetime
  stays with the application; reopening a Lab attaches it again.
- Reports also check outcome agreement, after reading and verifying artifact bytes.
  This rejects inconsistent historical/imported outcomes without mutating history
  or hiding hash errors. `evidence[].consistent` is an additive operational field;
  report bytes and packet hashes consequently change. No new scientific assurance
  is inferred from that flag.
- Registration of a typed contract artifact is shared by proposal and the Lab run/
  cancellation paths. This covers controller-created amendments without silently
  rewriting existing artifacts, adding tasks, carrying forward old approvals or
  executing an amendment. New tasks and a fresh digest-bound approval remain
  necessary; the original campaign and its artifacts stay immutable.
- Keep the audit's source-project/context memory policy: cross-project retrieval
  requires explicitly approved public de-identified methodology/claim sharing
  (SCIENCE section 7). A principal's restricted-source grant alone is insufficient.
  There is no application memory caller or live credential boundary in this pilot.
- Keep `live` as a recognized raw result label that the shared offline validator
  explicitly rejects. It is not an enabled live path. Recovery additionally binds
  provider identity and version: the built-in mock implementation, fixed wire
  reader backend, or frozen normalized recording determines them. Future live
  integration still requires separate authorization and tested/pinned interfaces.
- Trusted code can assign artifact assurance and operates SQLite directly. These
  checks catch controller/application mistakes; they do not protect against a
  malicious controller, host administrator or forged test runner. Container
  configuration and all blocked deployment/provider/GPU gates are unchanged.

## 2026-09-07 — messaging proposal consolidation (not implemented)

- Consolidate the assistant product and architecture in
  [proposals/MESSAGING.md](proposals/MESSAGING.md). Start with a deterministic local
  brief, persistent awaiting-you queue, and scoped milestone subscriptions. This
  is a proposed additional N0–N6 track, not a change to pinned pilot requirements.
- Model notification authority as a service-scoped operator grant independent of
  campaign execution approval. The proposed egress dispatcher receives approved
  envelopes, never raw controller/evidence databases. Actual enforcement and live
  delivery require separate authorized integration and deployment review.
- Account for separate controller/evidence event streams, bounded projection
  recovery, uncertain external sends, and explicit disclosure of even metadata.
  Provider acceptance, operator acknowledgement, scientific review, and execution
  approval are distinct. A freeze notification is not preregistration.
- Remote approval remains optional and independent of useful conversational
  assistance and literature discovery. No service, schedule, credential, runtime
  permission, provider integration, or host configuration changed in this task.

## 2026-09-07 — Telegram implementation plan (planning only)

- The operator selected Telegram and explicitly prohibited implementation in this
  task. [proposals/TELEGRAM_IMPLEMENTATION_PLAN.md](proposals/TELEGRAM_IMPLEMENTATION_PLAN.md)
  specializes the messaging proposal into T0–T5: read-only briefings, durable
  projection, delivery lifecycle, Telegram adapter, authorized activation, and
  limited typed replies. No runtime or service setup is authorized by the plan.
- Plan one bot, private operator chat, and scoped lab, with plain-text deterministic
  briefs and no model dependency. Use polling for enrollment and later commands;
  keep remote execution approval, attachments, other transports and literature
  outside the first messaging release.
- Start with dedicated read-only interfaces because existing Lab/store constructors
  initialize writable state. Share verified report semantics; label charged
  reservations accurately. Preserve separate source and transport/attention state.
- Explicitly handle ambiguous sends, Telegram inbound retention and idle cursor
  resets, stale revision acknowledgements, bounded caps, and credential rotation.
  Real egress requires reviewed operator grants and a measured identity boundary;
  the development campaign token cannot enable it. Bot credentials remain gateway
  authority and are not claimed to enforce recipient restrictions themselves.

## 2026-09-07 — Telegram messaging implementation (T0: read-only briefings)

Implementation was authorized for local development only. Creating a bot,
obtaining credentials, sending a message, installing a host service, deploying a
controller, enabling a live provider, expanding permissions, and pushing all
remain unauthorized and undone.

- One verified report interpretation now lives in `reporting.py`. `Lab.report`
  delegates to `build_report`, so the messaging surface cannot present a
  conclusion the authoritative report would refuse. Extraction is behaviour
  preserving: the existing tamper, attribution, invalidation and outcome-label
  regressions pass unchanged, including the in-place assurance assignment that
  precedes an evidence downgrade and the rule that a substantive finding is
  rendered only when assurance is `independently_recomputed`.
- `sources.py` opens the controller and evidence databases with `mode=ro` plus
  `PRAGMA query_only`, validates the controller `user_version` and the evidence
  table set, and issues only bounded `SELECT`s. It never reuses `Lab`,
  `Controller` or `Artifacts` constructors, which create directories, run DDL,
  set write pragmas and open a metadata write transaction. Operator credential
  metadata and the memory table are not observable through it at all; raw SQL
  reads would bypass the controller's memory authorization rules.
- This is an application-level boundary. A read-only connection to a live WAL
  database still maps the shared-memory index, so the `-shm` file's modification
  time changes even though no database content does. Tests therefore assert
  unchanged logical content rather than unchanged sidecar timestamps, and actual
  write denial remains a deployment-profile property, not a Python property.
- Source identity binds to the first append-only event row rather than a path,
  inode or modification time, because the controller has no durable source ID
  and triggers forbid updating or deleting events. Feed cutoffs are a per-store
  vector; no global snapshot transaction is claimed.
- A briefing renders only allowlisted typed fields with a named derivation.
  Reservations are stated as charged, never as measured runtime or cost. Signals
  with no producer — host telemetry, monetary cost, reading and acknowledgement,
  literature coverage — are listed as unavailable instead of zero. A campaign
  whose evidence fails verification becomes a bounded integrity item; it never
  keeps a previously green label.
- Messaging acceptance conditions live in the separate
  `specs/messaging-acceptance.json` and are emitted under a distinct JUnit
  property. The pinned first-pilot inventory, its manifest and `validate_pack`
  are unchanged; an unknown ID under the pinned `acceptance` property would
  otherwise become a release-gate integrity diagnostic.

## 2026-09-07 — existing-project integration

- Add selected-source snapshots as specified in PROJECT_INTEGRATION.md before
  implementation, preserving the complete-directory mode and pinned requirements.
- Connections identify source locations; revisions identify canonical contracts;
  snapshots bind revisions and selected bytes. No Git command or setup hook runs.
- Execution-only experiments share the controller ledger and operator approvals.
  They provide protocol-checked, self-reported project output, with no scientific
  evaluation claim. No live provider, implicit image pull, host fallback or deploy.

- Preserve the original validator itself as a pinned specification artifact. The
  new `tools/validate.py` wrapper changes only presentation: it removes the original
  kit's fixed `framework_implemented` declaration and emits `runtime_verification:
  not_run`. All original 46 requirements and original file hashes are retained.
- Preserve input invalidation as a launch blocker, but use the immutable controller
  contract for cancellation even when evidence was invalidated. A historical stop
  receipt does not restore validity. Both pre-launch and running-worker regressions
  cover this distinction.
- Keep execution-only outcome interpretation compatible with shared reports and
  briefings. Expose actual process success separately from protocol validity;
  scientific outcome stays `not_evaluated`, including a successful self-report.
