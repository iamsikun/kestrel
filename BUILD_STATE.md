# Build state

Telegram messaging T2 (2026-09-07): approved-envelope delivery on
`build/telegram-messaging`. New `src/kestrel/notifications.py` with operator-issued
channels and service grants, template-driven disclosure with classification
inheritance and release scanning, atomic envelope publication, 60-second release
permits, the PENDING/READY/SENDING/ACCEPTED/RETRY_WAIT/UNCERTAIN/terminal state
machine, daily and reserved-critical quotas, spool byte budget, single-sender
lease with uncertainty-preserving takeover, and offline `FakeTransport` /
`NoEgressTransport`. A separate messaging operator credential is provisioned by
`notify init`. `tests/test_notifications.py` adds 46 cases including a Hypothesis
delivery-lifecycle state machine. Commands and results:
`.venv/bin/ruff check src tests tools/release_gates.py tools/prepare_wheelhouse.py`
exit 0; `python3 tools/validate_pack.py` exit 0; `git diff --check` exit 0;
`KESTREL_TEST_SCOPE=core .venv/bin/python -m pytest -m 'not isolation and not install' -q`
— 362 passed, 1 skipped (A28), 12 deselected, exit 0. Evidence:
`/private/tmp/kestrel-telegram-messaging/core-t2.xml`.
Only the offline scripted transport exists; nothing was sent and no bot,
credential, service or deployment was created. N-A05 and N-A12 remain unpassed:
they need the Linux deployment profile and measured OS denial. Next action: T3,
the typed Telegram HTTPS adapter and pairing workflow against fake HTTP.

Telegram messaging T1 (2026-09-07): durable assistant state on
`build/telegram-messaging`. New `src/kestrel/messaging.py` with its own migrated
SQLite schema (source bindings, items and revisions, attention history,
subscriptions, versioned milestones, schedules, occurrences, briefings, intents,
processed requests, gaps), deterministic projection and rule engine, DST-correct
daily schedule with single catch-up, quiet-hour deferral, typed milestone
predicates, and `notify`/`inbox` CLI branches under a new `--messaging-root`.
`tests/test_messaging.py` adds 42 cases. Commands and results:
`.venv/bin/ruff check src tests tools/release_gates.py tools/prepare_wheelhouse.py`
exit 0; `python3 tools/validate_pack.py` exit 0; `git diff --check` exit 0;
`KESTREL_TEST_SCOPE=core .venv/bin/python -m pytest -m 'not isolation and not install' -q`
— 316 passed, 1 skipped (A28), 12 deselected, exit 0. Evidence:
`/private/tmp/kestrel-telegram-messaging/core-t1.xml`.
No envelope is exported and no transport exists yet; nothing was sent, no bot or
credential exists, and no service, deployment or permission changed. Next action:
T2, the approved-envelope spool, service grant, delivery state machine and fake
transport.

Telegram messaging T0 (2026-09-07): read-only briefings implemented on
`build/telegram-messaging`, branched from the planning branch tip `b944687`
(which carries an unrelated local installation note above `28ec895`; that work is
preserved, not dropped). New modules `src/kestrel/reporting.py`,
`src/kestrel/sources.py`, `src/kestrel/briefings.py`, a `kestrel --lab L brief
--since --format json|markdown|plain` CLI branch that never constructs `Lab`, a
separate `specs/messaging-acceptance.json` inventory, and `tests/test_briefings.py`.
`Lab.report` now delegates to the shared `build_report`; its output is unchanged.
Commands and results:
`.venv/bin/ruff check src tests tools/release_gates.py tools/prepare_wheelhouse.py`
exit 0; `python3 tools/validate_pack.py` exit 0 (20 files, 46 requirements
unchanged); `git diff --check` exit 0;
`KESTREL_TEST_SCOPE=core .venv/bin/python -m pytest -m 'not isolation and not install' -q`
— 274 passed, 1 skipped (A28), 12 deselected, exit 0, against the 249-pass
baseline. Evidence: `/private/tmp/kestrel-telegram-messaging/`.
No bot, credential, message, host service, deployment, live provider, permission
change or push occurred; the read-only reader is an application boundary, not a
measured OS one. Next action: T1, the assistant database, projection, rules,
milestones, schedules and durable inbox.

Local installation (2026-09-07): user requested installation and an empty lab at
`/Users/iamsikun/research/sklab`. Built source revision
`28ec895ee6335975ee9423a343ffcf70d337984e` with `uv build --offline` (exit 0),
then installed the wheel with `uv tool install --offline --no-config --no-build
--no-index --find-links /private/tmp/kestrel-wheelhouse --python
/Users/iamsikun/research/kestrel/.venv/bin/python
/Users/iamsikun/research/kestrel/dist/kestrel_research_runtime-0.1.0-py3-none-any.whl`
(exit 0). `kestrel lab init /Users/iamsikun/research/sklab`, `kestrel doctor`,
and `kestrel --version` passed (exit 0); executable is
`/Users/iamsikun/.local/bin/kestrel`, version 0.1.0. Lab mode is 0700 and operator
token mode is 0600; token contents were not displayed. No projects enrolled.
Clean-wheel installation and installed offline demo: `tests/test_install.py -q`
with KESTREL_WHEEL and KESTREL_WHEELHOUSE set to the above inputs passed (1 test,
exit 0). Initial sandboxed run failed with uncertain fixture termination (exit 1);
rerun with approved process-inspection access passed. Initial receipts:
`/private/tmp/kestrel-sklab-install-evidence.json` and
`/private/tmp/kestrel-sklab-install.xml`. Passing receipts and SHA256:
- `/private/tmp/kestrel-sklab-install-verified.json`:
  `24664c55b042c8e113de1e0c7c324673e85f9ba234d36c03b3ef9d49d39fb1fd`
- `/private/tmp/kestrel-sklab-install-verified.xml`:
  `ea7f6570188200f5d8dc659973c1e0ee7b094e904d53c1fc94c069d81637345b`
- Wheel: `e33177f6c7f4cdb14a4c512ce5564d3843707bea5621a709a79e2625f82eac79`.
No runtime changes; full static/core/state-machine/adversarial/isolation gates were
not rerun for installation. Prior deployment blockers remain. Installation task
complete; next action when requested is lab project setup using the documented
CLI. No live campaign or controller service was deployed.

Telegram implementation planning (2026-09-07): operator selected Telegram and
requested no implementation. [Implementation plan](docs/proposals/TELEGRAM_IMPLEMENTATION_PLAN.md)
defines one private chat, deterministic briefings, read-only source integration,
T0–T5 delivery slices, pairing/revocation, polling/recovery, tests and activation
gates. MESSAGING links to it; assumptions are in docs/DECISIONS.md. Documentation
only; no runtime, dependencies, bot, credentials, schedule, or service created.
Documentation verification receipt: `/private/tmp/kestrel-telegram-plan/checks.json`.
Plan SHA256: `55146fd0cc3a1354070a28c0f37006022515856c4e355f4fd31fcab49dc3b9b8`.
Pack integrity, local document links/fences/whitespace, and unchanged runtime/spec
checks: exit 0. Runtime, package-install, isolation and live Telegram/deployment
tests were not run for this planning task; their prior evidence/blockers remain.
Next action is review of the plan. Only after a request to implement, start T0's
read-only verified report/briefing slice with synthetic offline tests. Deployment,
user-data access and live integration gates remain separate and unchanged.

Messaging design consolidation (2026-09-07):
[docs/proposals/MESSAGING.md](docs/proposals/MESSAGING.md) now covers assistant
behaviors, source-aware projection/inbox, approved-envelope delivery, attention
and authorization boundaries, researched transport candidates, and proposed N0–N6
increments with 20 acceptance conditions. Proposal only; no runtime or pinned
specification changes. Consequential assumptions are in docs/DECISIONS.md.
Consolidation-time proposal SHA256: `b56721a3bc252f4b6bf1519f6dc91ceea7224ef13f84a7d1680c771901d31912`.
Check receipt and retained original draft: `/private/tmp/kestrel-messaging-review/`.
Pack integrity, Ruff, local document links and whitespace checks: exit 0.
Runtime unit/integration/state-machine/adversarial, install, isolation, live
transport, and deployment gates were not run for this documentation-only task;
prior evidence and blockers below remain unchanged. Temporary evidence may be
cleaned by the OS. Next unblocked messaging action: implement N0 deterministic
local briefing from verified records with synthetic offline tests. This proposal
does not authorize remote delivery, host service setup, or deployment.

Repository orientation (2026-09-07): [docs/REPOSITORY_GUIDE.md](docs/REPOSITORY_GUIDE.md)
maps the implemented modules, external lab layout, campaign flow, and personal-assistant
product gaps. Linked from USAGE; no runtime, original specification, or acceptance
changes. Inspected checkout: `a73f5387400f3d5eb9d024ee45782b98192b9e0e`.
Fresh core check: `KESTREL_SOURCE_REVISION=a73f538 KESTREL_TEST_SCOPE=core .venv/bin/python -m pytest -m 'not isolation and not install' -q --junitxml=/private/tmp/kestrel-structure-review-core-unrestricted.xml`
— exit 0, 249 passed / 1 skipped (A28) / 12 deselected, 18.79 seconds.
JUnit SHA256: `f19b3ab84ef403ffddf49c6c63954353c837c3b44f25a8b58975636ebe55fef6`.
Initial tool-sandbox run could not inspect process state (`ps`: operation not permitted);
interrupted with exit 2 after 5 failures / 48 passes, then rerun outside that sandbox.
Initial XML: `/private/tmp/kestrel-structure-review-core.xml`, SHA256
`c6fc2217d862b1147dfc54e5c6a55aa348d06df32070f40961c75be483c8e2d3`.
`python3 tools/validate_pack.py`, `.venv/bin/python -m kestrel doctor`,
`.venv/bin/ruff check src tests tools/release_gates.py tools/prepare_wheelhouse.py`,
documentation link checks, and `git diff --check`: exit 0. Install, isolation,
external audit, and release accounting were not rerun for this documentation task;
their prior evidence and all blockers below remain unchanged. The new core XML
uses the abbreviated source revision and is orientation evidence, not a new release
certification. Next implementation proposal is the guide's general isolated synthetic
campaign slice; the separate independent deployment review remains outstanding.

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
