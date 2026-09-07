# Verification of the Telegram messaging implementation

Tested revision: `b7fbb1948a2d1d097b4b720e8208606ae8147797` on
`build/telegram-messaging`, branched from the planning branch tip `b944687`
(which carries one unrelated local installation note above the planning commit
`28ec895`; that work is preserved, not dropped). The working tree was clean when
these suites ran. Verification completed 2026-09-07 local time.

No bot was created, no credential was obtained, no message was sent, no host
service was installed, no controller was deployed, no live provider was enabled,
no permission was expanded, and nothing was pushed. No ongoing user research
repository was read. All labs, messaging roots and synthetic projects were
generated in external temporary directories. Tests made no network request after
dependency setup.

## Results

| Check | Result | Exit |
|---|---|---|
| Core unit, integration, adversarial and Hypothesis state-machine suite | 434 passed, 2 skipped, 12 deselected | 0 |
| Fresh external offline wheel install and installed CLI demo | 1 passed | 0 |
| Actual Docker Linux VM isolation suite | 11 passed, 19 deselected | 0 |
| Ruff | Passed | 0 |
| Original specification integrity (`validate_pack.py`) | 20 files, 46 requirements unchanged | 0 |
| Offline source distribution and wheel build | Passed | 0 |
| `git diff --check` | Passed | 0 |
| Release-gate accounting | 42 passed, 4 blocked, no integrity issues | 1, expected |
| Offline pilot demo | 2 campaigns, 6 charged attempts, 0 provider calls, both `not_supported` | 0 |

The two core skips are `A28` (sanitized provider captures unavailable, unchanged
from before this work) and `tests/test_telegram_live.py`, which is collected and
skipped because its four required authorization inputs are absent. A skip is an
unsatisfied gate, not a pass.

The core suite grew from the 249-pass baseline to 434 passes. The 185 new cases
are `tests/test_briefings.py` (25), `tests/test_messaging.py` (42),
`tests/test_notifications.py` (46, including a Hypothesis delivery-lifecycle
state machine) and `tests/test_telegram.py` (71), plus the live file's guard
test. No existing test was weakened, removed, or re-mapped, and no requirement
mapping changed.

## Exact commands

```sh
export KESTREL_SOURCE_REVISION=b7fbb1948a2d1d097b4b720e8208606ae8147797
.venv/bin/ruff check src tests tools/release_gates.py tools/prepare_wheelhouse.py
python3 tools/validate_pack.py
git diff --check
KESTREL_TEST_SCOPE=core .venv/bin/python -m pytest -m 'not isolation and not install' -q \
    --junitxml=/private/tmp/kestrel-telegram-messaging-final/core.xml
uv build --offline
KESTREL_TEST_SCOPE=install \
  KESTREL_WHEEL=/Users/iamsikun/research/kestrel/dist/kestrel_research_runtime-0.1.0-py3-none-any.whl \
  KESTREL_WHEELHOUSE=/private/tmp/kestrel-wheelhouse \
  KESTREL_INSTALL_EVIDENCE=/private/tmp/kestrel-telegram-messaging-final/install-evidence.json \
  .venv/bin/python -m pytest tests/test_install.py -q \
    --junitxml=/private/tmp/kestrel-telegram-messaging-final/install.xml
KESTREL_TEST_SCOPE=isolation KESTREL_TEST_PROFILE=isolated-local \
  KESTREL_TEST_RUNTIME=linux-docker-vm KESTREL_RUN_ISOLATION=1 \
  KESTREL_DOCKER_IMAGE=sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca \
  .venv/bin/python -m pytest tests/test_runners.py -m isolation -q \
    --junitxml=/private/tmp/kestrel-telegram-messaging-final/isolation.xml
uv run --offline kestrel demo --offline
.venv/bin/python tools/release_gates.py \
    --junit /private/tmp/kestrel-telegram-messaging-final/core.xml \
    --junit /private/tmp/kestrel-telegram-messaging-final/install.xml \
    --junit /private/tmp/kestrel-telegram-messaging-final/isolation.xml \
    --source-revision $KESTREL_SOURCE_REVISION \
    --output /private/tmp/kestrel-telegram-messaging-final/release-gates.json \
    --blocker 'A28=Actual sanitized provider captures unavailable' \
    --blocker 'A30=Live provider test unauthorized and untested' \
    --blocker 'A31=Credential-boundary integration unauthorized, unimplemented, and untested' \
    --blocker 'A43=Target GPU untested'
```

The release-gate command intentionally exits 1 while `A28` is blocked. After the
isolation run, `docker container ls -a --filter label=kestrel.attempt` showed one
exited container created at 15:16, which predates this run by 36 minutes and did
not come from it; this run left none of its own.

## Evidence

Directory: `/private/tmp/kestrel-telegram-messaging-final/`. Per-slice JUnit
records from the incremental runs are in
`/private/tmp/kestrel-telegram-messaging/`. Both are temporary and subject to
ordinary system cleanup; preserve them externally for long-term provenance.

| File | SHA256 |
|---|---|
| `core.xml` | `fb7e1c6e9a943f2e68c0f7ca0125ad0c836f61db12a18cff02a80ce1847ddf05` |
| `install.xml` | `6dcfe29e7d2414e33842b6869dd227a8b1e2f95114a7102d84fa2e32ac0e6b16` |
| `isolation.xml` | `df95817058e1cc7406ccab2479f79d350b4dfdcb60463a357444aaab694bfdc9` |
| `release-gates.json` | `9bf680f936c35d92d9449635196fa2f5b4338aa3989b2c2e362718da94a4eee0` |
| `install-evidence.json` | `6b252d9605d0425ffa64e61e4bf146fc9e19061bee659f4c02590d8727a0cfc1` |
| `demo.json` | `e0ac6ce57137eb7bf9019f77ba7b5a401ef74850a52cb4c980026109f731ab84` |
| `offline-walkthrough.txt` | `a74f6bf1c89bce9be050325035ad3fc9c4ebb7ec6de2fef39a100752a15368fc` |
| `validate-pack.json` | `2d5bac91e08a9f628d2884d1542fe56f0fd37597fbed57710f3d229fe9f0aa2f` |

Wheel SHA256: `b71e2e3d0de7466cb1f068f75447768851775a9657e2159f4ae9a1bf2c40e47f`.
The wheel differs from the first pilot's because it now also contains
`reporting.py`, `sources.py`, `briefings.py`, `messaging.py`, `notifications.py`
and `telegram.py`. Runtime dependencies are unchanged: Pydantic and PyYAML only.
Hashes establish content identity, not authority or scientific validity.

`offline-walkthrough.txt` records a full offline run against the demo lab: a
markdown briefing, messaging initialization, projection, the inbox, an
operator-issued channel and grant, envelope export, gateway intake and dispatch
over the fake transport, the released envelope's exact text, the recorded API
assumptions, a refused live operation, and the resulting status.

## The offline pilot is unchanged

The demo completed two campaigns with six charged attempts, zero provider calls,
and two `not_supported` findings with `independently_recomputed` assurance —
identical in every scientific axis to the pre-messaging baseline. `Lab.report`
now delegates to `reporting.build_report`; its output is unchanged, and the
existing tamper, borrowed-evidence, invalidation and outcome-consistency
regressions pass without modification.

## What is verified, and what is not

Verified offline, with deterministic clocks and injected failures:

- The read-only readers observe live-WAL and closed stores without changing any
  logical research content, and refuse writes, unsupported schemas, redirected
  paths and operator credential metadata.
- Briefings reproduce the authoritative report exactly, downgrade on
  invalidation, turn tampered or deleted evidence into integrity items, label
  missing telemetry as unavailable, and reproduce a stable identity for
  unchanged source state.
- Projection is replay-idempotent, resolves only from source state, groups a
  single invalidation and an attempt storm into one incident each, pauses on a
  replaced or truncated ledger until an explicit rebind, and emits one catch-up
  brief across missed days, DST folds and gaps, and a clock rollback.
- Delivery commits `SENDING` before the transport call, keeps ambiguity
  uncertain with no automatic resend, refuses to treat a lease as a provider
  fence, blocks on revocation, expiry, version supersession, missing permits and
  unavailable policy, and queues at its caps rather than emitting an overflow.
- Disclosure refuses above-ceiling content including identifier-only views, and
  the release scanner catches seeded paths, URLs, tokens and secret-shaped
  strings; clipping preserves the caveat tail.
- The Telegram adapter's request and response handling matches the documented
  shapes, exposes only four methods, refuses redirects and foreign origins, and
  keeps the token out of every log, repr and exception.
- Pairing stops on a foreign webhook, stores only a nonce hash, and requires a
  local operator confirmation. Inbound polling journals before advancing the
  offset, rebases on the documented idle reset, and no execution authority is
  reachable from an inbound message.

Not verified, and explicitly unpassed:

| Condition | Missing |
|---|---|
| N-A05 | The Linux deployment profile installed and cross-identity read/write denials actually measured |
| N-A12D | Enforced egress restriction measured on a deployed host under the actual service identity. The code-level half (N-A12) is tested offline; a Python-side host check is not containment |
| N-A19 (live half) | Any evidence about a real provider, device, or person |
| N-A20 | Deferred with the N6 literature increment |
| A28, A30, A31, A43 | Pre-existing first-pilot blockers, unaffected by this work |

`deploy/telegram/` has never been executed on any host. The messaging boundary
demonstrated here is what each process is *given*, not what an operating system
*denies*. The messaging operator credential demonstrates service-grant policy in
a developer lab; it is not a deployed service identity. Passing these tests
clears no unrelated release gate, and the author of this work cannot also be its
independent review.
