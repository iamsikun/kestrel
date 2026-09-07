# Build state

Telegram messaging audit repairs completed offline on `build/telegram-messaging`
(2026-09-07). All nine findings have code repairs and 30 new regression cases.
[docs/MESSAGING_REPAIRS.md](docs/MESSAGING_REPAIRS.md) records implementation,
commands, outcomes, evidence and limitations. The original findings are retained
in [docs/MESSAGING_AUDIT.md](docs/MESSAGING_AUDIT.md).

Verified on the retained source snapshot before commit (JUnit revision
`uncommitted`; exact input hashes in
`docs/audits/telegram-messaging-repair/source-sha256.json`):

- Core unit/integration/adversarial/state-machine suite: 464 passed, 2 skipped,
  12 separately executed tests deselected; exit 0.
- Fresh offline wheel build and external package install/installed demo: passed;
  1 install test, exit 0.
- Actual Docker Linux VM runner isolation: 11 passed, exit 0.
- Ruff, original pack integrity (20 files / 46 requirements), whitespace: exit 0.
- Release accounting: 42 passed / 4 blocked, no integrity issues; exit 1.
  All composite readiness flags and deployment.authorized remain false.

Evidence: `/private/tmp/kestrel-messaging-repair/`; retained manifests in
`docs/audits/telegram-messaging-repair/`. Temporary evidence may be cleaned by the
system. Wheel SHA256:
`d77e76427e0d212dc48061fa9b765a1d085cbeebf77677a7cf9ed325f923509e`.

Repairs: mandatory disclosure provenance; gateway-only CLI/routing and shared
file modes; durable inbound failure recovery; unique sender ownership, OS lock
and conditional claims; releasable briefs; grant/projection expiry enforcement;
combined quotas, persisted pacing/reply-minute cap; forwarded-command rejection.
Research-derived messages conservatively inherit whole-lab classifications; this
can suppress public-only delivery when any restricted material is present.
Consequential choices: [docs/DECISIONS.md](docs/DECISIONS.md).

Remaining gates: A28 sanitized provider captures; A30 live provider integration;
A31 actual credential-boundary integration; A43 target GPU. Messaging N-A05 and
N-A12D require real target-host identity and egress measurements; N-A19's live half
is unpassed, N-A20 literature work is deferred. Linux VM runner measurements do
not certify a deployed messaging service. T4 service files remain inert.

Next unblocked action: independently review these repairs and their regressions.
Then, only with separate authorization, follow
[docs/MESSAGING_OPERATIONS.md](docs/MESSAGING_OPERATIONS.md) for target-host gates
and a synthetic live Telegram test. No live message, host-service change,
controller deployment, permission expansion or research-repository access occurred.

Earlier implementation evidence remains in
[docs/MESSAGING_VERIFICATION.md](docs/MESSAGING_VERIFICATION.md),
[docs/VERIFICATION.md](docs/VERIFICATION.md), and
[docs/REVIEW_FOLLOWUP.md](docs/REVIEW_FOLLOWUP.md). Those historical claims apply to
their recorded revisions; the independent messaging audit superseded several of
the original messaging claims.
