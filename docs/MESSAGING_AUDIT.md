# Independent Telegram messaging audit — 2026-09-07

Follow-up: [MESSAGING_REPAIRS.md](MESSAGING_REPAIRS.md) records subsequent fixes
and regression evidence. The findings below describe the original audited revision.

**Assessment: changes required before activation.** Nine confirmed findings below
contradict important delivery, disclosure, or usability claims despite the existing
suite passing. This review does not authorize deployment or live messaging.

Reviewed `build/telegram-messaging` at `03a0f0f`. Runtime/tests/specifications match
`52a2d685ad8a74ad299ee4b35e4f67bb048fbfde`; the intervening changes are documentation.
The review covered the implementation plan, new messaging modules, CLI, tests,
verification claims, operations guide, and proposed service/ownership profiles.
Runtime code and existing tests were not modified. All probes used generated,
non-sensitive fixtures outside the checkout and fake transports; even the artifact
labeled `restricted` contains only synthetic audit bytes.

## Findings

### 1. P1 — Projected restricted metadata is exported as public

`messaging.py:953` stores inherited classifications under `payload.detail`, while
`notifications.py:283` reads only `payload.classifications`, defaulting to
`public_synthetic`. Invalidating a synthetic artifact labeled `restricted` produced
an evidence-correction intent whose detail retained that label, yet export emitted
its affected-record count and attention reference as `public_synthetic` under the
default public-only grant. The leak demonstrated is event metadata, not artifact
contents. It violates the explicit prohibition on metadata-only disclosure above
the grant ceiling. Backfill summaries and briefing/reply paths also need a complete
classification audit; they cannot safely infer public content from absent labels.

Repair: carry a mandatory, validated aggregate classification through projection,
intent creation and rendering; fail closed when provenance is missing. Regression
tests must start from classified source records and exercise actual export, rather
than injecting labels directly into hand-built renderer inputs.

### 2. P1 — The gateway CLI cannot run with its declared access restrictions

`cli.py:116` opens `Assistant` before gateway intake, dispatch, or inbound polling.
That reads private configuration and opens the writable assistant authority DB.
`cli.py:60` and transport construction also obtain pairing information from private
assistant metadata. The proposed gateway/poller identity is expressly denied those
files by `deploy/telegram/ownership.md` and the service's `InaccessiblePaths`.
A denied-Assistant-access probe makes even offline gateway intake exit 2.

Separately, `notifications.py:371` writes exported policy, permits and envelopes
with mode `0600`; the ownership matrix requires group-readable `0640`. The actual
probe output confirms `0600` for all three. Setgid directories do not add file
read permission, and each rewrite replaces any manual file-mode correction.

Repair: dispatch gateway-only CLI paths without constructing Assistant; publish
only the minimal validated routing/pairing configuration the gateway needs, under
appropriate authority-owned permissions. Make atomic publication preserve the
declared read-only sharing. Test real cross-identity denial and successful gateway
operation together. Granting the gateway access to assistant-private is not a fix.

### 3. P1 — Inbound storage failure silently acknowledges an unsaved command

`telegram.py:877` catches every insertion exception as a duplicate, then advances
the offset. A synthetic SQLite trigger that aborts insertion of update 8 resulted
in `accepted=0`, zero journal rows, `duplicates=[8]`, and persisted `offset=9`.
On the next poll, that offset confirms the lost command to Telegram, including
potentially `/stop`. This consequence follows Telegram's documented
[getUpdates offset semantics](https://core.telegram.org/bots/api#getupdates).

Repair: distinguish a verified existing journal identity from storage errors.
Advance only across durably recorded updates/tombstones; stop and retain the
safe cursor on failed persistence. Test transient failure, replay, and recovery.

### 4. P1 — Concurrent dispatch can send one envelope twice

`cli.py:199`/`:207` assigns every CLI gateway instance the same owner, `cli`.
`notifications.py:896` treats that owner as permission to reacquire a live lease.
Candidate selection occurs outside the claim transaction, and the transition to
`SENDING` at `notifications.py:1025` does not condition on the expected prior state.

The probe deterministically interleaves two instances after candidate selection:
both report fake-provider acceptance of the same envelope, and the durable row
ends with `transmissions=2`. This is a local concurrency defect, independent of
the unavoidable uncertainty of a real provider request.

Repair: use unique process ownership and atomic conditional claims; revalidate
lease ownership/state when claiming. Add two-sender interleaving and takeover
tests that preserve uncertain outcomes without permitting automatic duplicate sends.

### 5. P1 — Daily briefs and requested briefs fail release scanning

`messaging.py:1098` and `:1529` pass the absolute lab path as the briefing label;
`briefings.py:372` includes it in outgoing plain text. The release scanner then
rejects the absolute path, and export suppresses the intent. The scheduled-brief
probe created one due occurrence and received `disclosure_refused: absolute_path`.
The `/brief` reply uses the same rendering path.

Repair: use a validated releasable display label or omit the local path from the
transport view, keeping full local provenance separately. Test scheduled and
requested briefs end to end through export and fake dispatch. Keep path scanning.
Initialization also leaves the schedule unset; the operations guide's explicit
`notify schedule set` step is currently necessary even when init accepts a time.

### 6. P1 — A fresh permit can outlive its service grant

`notifications.py:618` sets permit expiry to issuance plus 60 seconds without
clamping it to grant expiry. `dispatch_once` checks policy versions, but never
the policy grant's `expires_at`. With a grant ending at time 1,000,010 and a permit
issued at 1,000,001, dispatch at 1,000,011 still reports acceptance. This requires
only that the next refresh has not occurred; absolute expiry is already known.

Repair: clamp permit expiry to all applicable authority/intent deadlines and
check current grant expiry at each transmission. Do not reuse one frozen time
and policy for a potentially lengthy batch. Test the boundary with refresh delayed.

### 7. P1 — Permit refresh bypasses the stale-projection safeguard

Initial export checks projection freshness, but `notifications.py:587`
`refresh_permits` does not. With the projector stopped, the separately scheduled
permit refresher continues renewing releases for previously exported messages.
At 1,000 seconds after the last reconciliation, the probe recorded
`projection_stale=true`, one renewed permit, and an accepted fake send.

Repair: withdraw permits when projection health, source binding, or freshness
cannot be established. Bind release validation to the relevant current projection
and policy state. Test a stopped projector with a healthy independent refresher.

### 8. P2 — Ordinary traffic can exceed the combined automated daily cap

`notifications.py:949` checks combined critical/ordinary usage only for critical
traffic. Ordinary traffic checks its own allowance alone. Charging 20 critical
attempts followed by 15 ordinary attempts permits all 35, despite the configured
20-attempt automated cap with five reserved critical slots. The separate 40-attempt
hard cap still applies; this finding does not claim unlimited sending.

Repair: enforce the combined automated ceiling for both classes, then enforce
the ordinary reservation. Test both traffic orderings and retries. Static review
also finds no enforcement of the exported `reply_per_minute` cap or the plan's
one-message-per-second pacing in the dispatch loop; cover these in the repair.

### 9. P2 — Forwarded text is accepted as an operator command

`telegram.py:737` validates the current chat/sender but ignores forwarding metadata.
The probe forwards synthetic `/stop` text from another user through the registered
operator's private chat; normalization accepts it as a normal command. The plan
requires forwarded commands to be rejected. This is not arbitrary foreign-user
access: it requires the registered user to forward the message.

Repair: reject forwarding markers before normalization discards them and persist
a rejection tombstone. Test the documented
[Message.forward_origin field](https://core.telegram.org/bots/api#message), along
with supported legacy/automatic-forward forms where applicable.

## Verification and retained evidence

| Check | Result |
|---|---|
| Existing core unit/integration/state-machine suite | 434 passed, 2 skipped, 12 deselected; exit 0 |
| Independent synthetic probes | Exit 0; observations reproduce the findings above, not passing acceptance gates |
| Fresh offline wheel build | Exit 0 |
| External offline package install and installed demo | 1 passed; exit 0 |
| Ruff | Exit 0 |
| Original pack integrity | 20 files / 46 requirements unchanged; exit 0 |
| Diff whitespace | Exit 0 |
| Live Telegram and target Linux messaging identities/egress | Not run; remain unsatisfied |
| Existing Docker runner isolation suite | Not rerun in this messaging audit; prior evidence is not new certification |

The two skips are the unavailable A28 captures and the gated live Telegram file.
The initial sandboxed core run encountered fixture process-inspection restrictions
and was interrupted (4 failures, 47 passes, exit 2). The complete rerun with process
inspection allowed produced the passing result above. These environment failures
are not counted as messaging defects. One initial probe run had a harness-only
context-manager error; the retained corrected script ran successfully.

Reproductions and machine-readable observations are retained in
[`audits/telegram-messaging/probes.py`](audits/telegram-messaging/probes.py) and
[`probe-results.json`](audits/telegram-messaging/probe-results.json).
[`sha256.json`](audits/telegram-messaging/sha256.json) records their hashes and
those of the wheel, JUnit records and installation evidence. Temporary evidence
is under `/private/tmp/kestrel-messaging-audit/` and may be cleaned by the system.
The probe script writes synthetic runtime state only under `/private/tmp`.

Commands, from the checkout:

```sh
mkdir -p /private/tmp/kestrel-messaging-audit
KESTREL_SOURCE_REVISION=03a0f0f KESTREL_TEST_SCOPE=core .venv/bin/python -m pytest -m 'not isolation and not install' -q --junitxml=/private/tmp/kestrel-messaging-audit/core-unrestricted.xml
PYTHONPATH=src .venv/bin/python docs/audits/telegram-messaging/probes.py
.venv/bin/ruff check src tests tools/release_gates.py tools/prepare_wheelhouse.py
python3 tools/validate_pack.py
uv build --offline
KESTREL_SOURCE_REVISION=03a0f0f KESTREL_TEST_SCOPE=install KESTREL_WHEEL=/Users/iamsikun/research/kestrel/dist/kestrel_research_runtime-0.1.0-py3-none-any.whl KESTREL_WHEELHOUSE=/private/tmp/kestrel-wheelhouse KESTREL_INSTALL_EVIDENCE=/private/tmp/kestrel-messaging-audit/install-evidence.json .venv/bin/python -m pytest tests/test_install.py -q --junitxml=/private/tmp/kestrel-messaging-audit/install.xml
git diff --check
```

## Repair handoff

Keep live activation disabled. First repair classification propagation, process
separation, durable inbound handling and atomic sender claims; turn the probes
into end-to-end regressions asserting the intended safe behavior. Then repair
brief rendering, authority freshness, quotas and forwarded-message rejection.
Re-audit these paths and update the implementation verification claims before
requesting any separately authorized Linux deployment or synthetic Telegram test.
This review is not an exhaustive proof of correctness or a deployment approval.
