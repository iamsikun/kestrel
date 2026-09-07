# Telegram messaging audit repairs — 2026-09-07

All nine findings in [MESSAGING_AUDIT.md](MESSAGING_AUDIT.md) have code repairs
and offline regression coverage. Live messaging remains disabled. This repair
record is not an independent deployment approval.

## Changes

| Finding | Repair and regression |
|---|---|
| Restricted metadata export | Mandatory provenance at rendering; all research-derived intents conservatively inherit whole-lab classifications, including unassociated evidence. Tests start from actual classified artifacts and cover correction, backfill, brief, inbox and status views. |
| Gateway/private authority coupling | Gateway CLI paths never instantiate Assistant; confirmed numeric routing is separately exported. Export files are 0640, shared runtime DB/lock files 0660. Tests remove private config/state paths and run gateway intake, fake dispatch, health, delivery inspection and injected inbound polling successfully. |
| Lost inbound commands | Only a durably existing update identity is a duplicate. Storage failure propagates without advancing the batch cursor. A failed middle-of-batch `/stop` is recovered and applied on replay. |
| Duplicate sends | Unique owners, nonblocking OS file lock, and conditional transactional claims. Tests cover same-label instances, overlap after lease expiry, and a changed candidate; only one attempt is charged/sent. |
| Unsendable briefs | Outgoing plain-text briefs omit local lab paths while full local records retain provenance. Scheduled and requested briefs both reach the fake transport. |
| Grant expiry | Permit expiry is clamped to grant/intent/projection deadlines; each candidate rechecks current time and policy. Tests cover expiry and mid-batch revocation. |
| Stale projection permits | Renewal requires fresh, bound, available sources whose heads match the projection. Tests stop projection, change sources, remove access and require rebinding. Legacy intents without provenance cannot regain permits. |
| Quota overflow | Both critical and ordinary attempts count against the combined automated cap. Added persisted one-second pacing and rolling-minute reply limits. Tests check both class orderings and gateway restarts. |
| Forwarded commands | Forwarding markers are rejected before wire normalization drops them; rejection tombstones still permit safe cursor advancement. Forwarded `/stop` never changes attention state. |

Implementation choices and limitations are recorded in
[DECISIONS.md](DECISIONS.md). Whole-lab classification is intentionally conservative:
a restricted record can suppress an otherwise public campaign's message. Narrower
attribution is future work, not permission to drop classifications.

## Verification

Tests ran on the working source snapshot before commit, with JUnit revision
`uncommitted`. The retained source hash manifest identifies the exact runtime,
test, specification, tool and packaging inputs; documentation edits after the
run do not change those inputs. No test result was relabeled to imply a run at
a different revision.

| Gate | Result | Exit |
|---|---|---|
| New audit regressions | 30 passed | 0 |
| Full core unit/integration/adversarial/state-machine suite | 464 passed, 2 skipped, 12 deselected | 0 |
| Offline wheel/sdist build | Passed | 0 |
| Fresh external offline package install and installed demo | 1 passed | 0 |
| Existing actual Docker Linux VM isolation suite | 11 passed, 19 deselected | 0 |
| Ruff | Passed | 0 |
| Original specification integrity | 20 files / 46 requirements unchanged | 0 |
| Diff whitespace | Passed | 0 |
| Release accounting | Existing unsatisfied gates remain; no deployment authorization | 1 |

Two core skips remain: A28 sanitized provider captures and the gated live
Telegram test. Target-host messaging identity/egress conditions N-A05/N-A12D and
the live half of N-A19 remain unpassed. Docker runner isolation is not measurement
of the messaging service profile. A30, A31 and A43 remain blocked.

An initial sandboxed selected-suite run was interrupted after fixture failures
and slow temporary-directory cleanup (exit 130). The complete selected suite
was rerun with process inspection available: 182 passed and two fixture failures
caused by newly enforced provenance/pacing. Their fixture repairs preserve the
original assertions, and the full final suite above passes. An initial new
regression run was 24 passed / 1 failed because its mocked poll CLI lacked the
required credential-file argument; the corrected test uses a dummy path and an
injected client. These intermediate failures are not recorded as passes.

## Evidence and commands

Evidence directory: `/private/tmp/kestrel-messaging-repair/` (subject to system
cleanup). `source-sha256.json` records tested input content, and `sha256.json`
records JUnit/install/release evidence and wheel hashes. Copies of both manifests
are retained in `docs/audits/telegram-messaging-repair/`.

Wheel SHA256:
`d77e76427e0d212dc48061fa9b765a1d085cbeebf77677a7cf9ed325f923509e`.

```sh
.venv/bin/ruff check src tests tools/release_gates.py tools/prepare_wheelhouse.py
python3 tools/validate_pack.py
.venv/bin/python -m pytest tests/test_messaging_audit_regressions.py -q --basetemp=/private/tmp/kestrel-msg-regressions-third
KESTREL_TEST_SCOPE=core .venv/bin/python -m pytest -m 'not isolation and not install' -q --basetemp=/private/tmp/kestrel-msg-repair-core --junitxml=/private/tmp/kestrel-messaging-repair/core.xml
uv build --offline
KESTREL_TEST_SCOPE=install KESTREL_WHEEL=/Users/iamsikun/research/kestrel/dist/kestrel_research_runtime-0.1.0-py3-none-any.whl KESTREL_WHEELHOUSE=/private/tmp/kestrel-wheelhouse KESTREL_INSTALL_EVIDENCE=/private/tmp/kestrel-messaging-repair/install-evidence.json .venv/bin/python -m pytest tests/test_install.py -q --basetemp=/private/tmp/kestrel-msg-repair-install --junitxml=/private/tmp/kestrel-messaging-repair/install.xml
KESTREL_TEST_SCOPE=isolation KESTREL_TEST_PROFILE=isolated-local KESTREL_TEST_RUNTIME=linux-docker-vm KESTREL_RUN_ISOLATION=1 KESTREL_DOCKER_IMAGE=sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca .venv/bin/python -m pytest tests/test_runners.py -m isolation -q --basetemp=/private/tmp/kestrel-msg-repair-isolation --junitxml=/private/tmp/kestrel-messaging-repair/isolation.xml
.venv/bin/python tools/release_gates.py --junit /private/tmp/kestrel-messaging-repair/core.xml --junit /private/tmp/kestrel-messaging-repair/install.xml --junit /private/tmp/kestrel-messaging-repair/isolation.xml --source-revision uncommitted --output /private/tmp/kestrel-messaging-repair/release-gates.json --blocker 'A28=Actual sanitized provider captures unavailable' --blocker 'A30=Live provider test unauthorized and untested' --blocker 'A31=Credential-boundary integration unauthorized and untested' --blocker 'A43=Target GPU untested'
git diff --check
```

Next action: independently review the repairs and retained regressions. Any
target-host installation, real bot credential, live Telegram test, or controller
deployment still requires its separate authorization and measured gates.
