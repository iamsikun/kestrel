# Verification of the first pilot

The tested implementation revision is `7db8a6cb81c6805373889424c23151b58b63997b`
on `build/first-pilot`, which adds independent audit repairs on top of the previously
verified `2157914e776b42112e00ec3a760eec7fc024022a`. Verification completed on
2026-09-07 local time (2026-09-07 UTC). Subsequent documentation commits do not change
this tested code. The implementation is functional, but the complete acceptance gate
remains blocked, and this revision has no independent review because its own auditor
wrote the repairs.
No deployment, publication, private research access, live provider call, cloud job,
or host-service/driver modification was performed.

## Results and acceptance accounting

All runtime suites ran serially against that committed source; the source tree
was clean. Tests generated external synthetic projects and labs. No user research
repository was read or used. Tests and fixture campaigns made no external network
requests after setup; isolated egress checks used local test endpoints.

| Check | Result | Exit status |
|---|---|---|
| Core unit, integration, adversarial and Hypothesis state-machine suite | 238 passed, 1 skipped, 12 deselected | 0 |
| Fresh external offline wheel install and installed CLI demo | 1 passed | 0 |
| Actual Docker Linux VM isolation suite | 11 passed, 19 deselected | 0 |
| Ruff | Passed | 0 |
| Original specification integrity | 20 files, 46 requirements unchanged | 0 |
| Offline source distribution and wheel build | Passed | 0 |
| Release-gate report | 42 requirements passed, 4 blocked | 1, expected |

The eight tests added since `2157914` are independent-audit regressions mapped to A27,
A29, A40 and A45; they cover the completion invariants, report attribution, cross-project
memory scope and provider labelling described below. No requirement mapping was removed.

The core skip is A28: actual sanitized Codex and Claude captures are unavailable.
The 12 deselections in the core command are the separately executed install and
isolation tests; they are not skipped deployment evidence. The isolation command
selects 11 container tests and deselects 19 already covered development tests.
Core duration was 16.33 seconds; install 2.41 seconds; isolation 17.64 seconds.
Afterward, `docker container ls -a --filter label=kestrel.attempt` returned no
remaining test containers (exit 0).

The report derives 36 passing core requirements and six passing isolation
requirements from actual JUnit records. A28, A30, A31 and A43 are blocked; no other
requirement is missing or failed. There are no execution-provenance integrity
issues. `readiness.offline_pilot` remains false because A28 is a required core gate;
all composite readiness flags are consequently false. The measured isolation
subgate is passed for the Docker Linux VM only. `deployment.authorized` is false.
A marker establishes only what its test actually exercises, not general security
or scientific correctness. The original pack validator still says it verifies a
specification, not an implementation; runtime evidence comes from these suites.

## Exact reproduction

Setup and runnable demo from this checkout:

```sh
uv sync --locked --python 3.13
uv run --offline kestrel doctor
uv run --offline kestrel demo --offline
```

The installed-package gate ran the console entry point from a fresh external
working directory, fresh venv and empty installer cache, using `--offline`,
`--no-index`, `--no-build` and an existing hash-verified wheelhouse. A trusted Python
audit hook denied socket activity in the CLI and generated fixture workers; this
is offline assurance for trusted code, not adversarial process isolation.
Detailed nested commands, imports, versions, exit statuses and stdout/stderr hashes
are retained in `install-evidence.json`.

The exact final suite commands used these environment values:

```sh
export KESTREL_SOURCE_REVISION=7db8a6cb81c6805373889424c23151b58b63997b
KESTREL_TEST_SCOPE=core .venv/bin/python -m pytest -m 'not isolation and not install' --junitxml=/private/tmp/kestrel-verification-audit-fixes/core.xml -q
uv build --offline
KESTREL_TEST_SCOPE=install KESTREL_WHEEL=/Users/iamsikun/research/kestrel/dist/kestrel_research_runtime-0.1.0-py3-none-any.whl KESTREL_WHEELHOUSE=/private/tmp/kestrel-wheelhouse KESTREL_INSTALL_EVIDENCE=/private/tmp/kestrel-verification-audit-fixes/install-evidence.json .venv/bin/python -m pytest tests/test_install.py -q --junitxml=/private/tmp/kestrel-verification-audit-fixes/install.xml
KESTREL_TEST_SCOPE=isolation KESTREL_TEST_PROFILE=isolated-local KESTREL_TEST_RUNTIME=linux-docker-vm KESTREL_RUN_ISOLATION=1 KESTREL_DOCKER_IMAGE=sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca .venv/bin/python -m pytest tests/test_runners.py -m isolation -q --junitxml=/private/tmp/kestrel-verification-audit-fixes/isolation.xml
.venv/bin/ruff check src tests tools/release_gates.py tools/prepare_wheelhouse.py
python3 tools/validate_pack.py
```

Do not set a previous source revision when testing changed code. The fixture
supervisor needs permission to inspect its own process tree; when the development
tool sandbox denied that earlier, the attempt stayed uncertain and retained its
reservation. That run was not treated as a successful execution gate.

To reproduce the acceptance calculation from the final records:

```sh
.venv/bin/python tools/release_gates.py --junit /private/tmp/kestrel-verification-audit-fixes/core.xml --junit /private/tmp/kestrel-verification-audit-fixes/install.xml --junit /private/tmp/kestrel-verification-audit-fixes/isolation.xml --source-revision 7db8a6cb81c6805373889424c23151b58b63997b --output /private/tmp/kestrel-verification-audit-fixes/recomputed-gates.json --blocker 'A28=Actual sanitized provider captures unavailable' --blocker 'A30=Live provider test unauthorized and untested' --blocker 'A31=Credential-boundary integration unauthorized and untested' --blocker 'A43=Target GPU untested'
```

This command intentionally exits 1 while A28 is blocked. The retained report
additionally fingerprints the wheel.

## Demo evidence and hashes

Evidence directory: `/private/tmp/kestrel-verification-audit-fixes/`. Runtime data are
external to the framework checkout. These local temporary files may be removed
by normal system cleanup; preserve them externally if long-term retention is needed.

The demo completed two campaigns with six charged attempts: two finite offline agent
tasks and four real fixture worker executions. All stopped and released their
reservations. No provider calls or tokens were consumed. Numerical baseline/treatment
errors were 0 and 4; counterexample counts were 0 and 2. Both valid findings are
`not_supported`, and each report's cited evidence is attributable to its own campaign.
This is exhaustive finite synthetic verification, with no population inference or claim
about model intelligence.

`demo.json` retains the full reports, and `evidence-packet.zip` is the exported packet
for the demo's last campaign. Exports are scoped by campaign provenance and exclude
operator tokens and unrelated artifacts. Imports retain imported assurance.

Wheel SHA256: `65759b17ee1025faaff2407784f3a17927236ab70cf03cc52ac70950f45c41d9`.

| Evidence file in the directory above | SHA256 |
|---|---|
| `core.xml` | `1dc28e2789d3b042b613d6ca5ad976473577d5e24986c82ff6d509dc64b73b35` |
| `install.xml` | `d41225db316ee5a6a4626fb855814bdc5d04e414b3f0b9f3f0a774f9579ba272` |
| `isolation.xml` | `e4ffde4cefbb9ee7a27794d656200d0bf8edd84ebb7cd52d30b8f7f241389810` |
| `install-evidence.json` | `1363c1ef027d1d72da6f6a722952caa03e102f1fb803602f9ef03d847271658f` |
| `demo.json` | `3232a690b499e632b95e98e5365ae450068d280f2704758b1dcd3bb7a8e456b9` |
| `evidence-packet.zip` | `f05052420cb75e75fe3d838effab825f0738a8075ec0abe4fd1786d0b7d6908a` |
| `release-gates.json` | `37ad7b7e9bd2208e2f10a1351da61ced6574de8364beb33f59dfaa3b9718f6ef` |

`sha256.json` and `sha256.txt` list all final record hashes. Hashes establish content identity,
not authority, authenticity or scientific validity.

## Tested platform and enforced scope

Host: macOS 26.5.2 arm64; Python 3.13.9; uv 0.9.3. Locked runtime versions tested
by clean installation: Pydantic 2.13.5, pydantic-core 2.46.5, PyYAML 6.0.3,
annotated-types 0.8.0, typing-extensions 4.16.0 and typing-inspection 0.4.4.
Core imports did not require provider SDKs, Docker SDK, ML frameworks or credentials.
No provider SDK or model version has a working live integration test or a working
version pin. The offline reader fixtures are explicitly synthetic.

Container measurements used existing Docker Engine 29.7.2, Linux kernel
7.0.12-linuxkit, arm64, cgroup v2, with the exact local public image digest shown
in the command above. No image pull was performed. The tests actually exercised
forbidden controller/peer/target/credential reads and writes, inspected mounts,
denied parent and child network requests to a local endpoint, measured CPU
throttling, RAM/PID exhaustion, read-only root and capped tmpfs behavior, advisory
workspace saturation, deadline/restart recovery and whole-container cancellation.
They include a candidate that stops its own in-container timer and dispatch races.

The developer CLI accepts only exact generated fixture programs. It has no OS
adversarial isolation. The Docker driver is a tested component, but arbitrary
isolated campaign execution and a separately operated controller service are not
enabled by the application. Writable bind storage is advisory; hard-quota requests
fail closed. Deadline supervision assumes a responsive daemon and external host
supervisor. Unknown jobs hold capacity. Per-controller reservations cannot limit
other applications or independently started controllers. The finite offline-agent
reader measures elapsed time after returning; it is not a hard asynchronous timer.

## Review history and remaining operator gates

An independent read-only audit of `f5bc705` found delayed launch after cancellation
and merged lineage for identical content. Both were repaired in `a75fb83`, with
regressions and an independent static-only recheck. Earlier assisted review also
found and repaired scoped export, imported assurance, stale-history, state-path
and hash-only claim issues. The later offline-agent/conformance additions received
author/root review and runtime tests, not a new independent deployment audit.

- Original independent report: `/private/tmp/kestrel-independent-review-fxaOwH/audit/REPORT.md`, SHA256 `ea6f62487b682d606235cad1a92ca09fe9cc9ab7792384db916157642406121c`.
- Static recheck: `/private/tmp/kestrel-independent-recheck-mhVoWd/RECHECK.md`, SHA256 `5944177745b82784d32b60b9f32b995e9ec094de2735d8c8717c8168c60c5584`.

A second independent audit reviewed `e03b73f` read-only in a separate workspace. It
reproduced the core, install and isolation suites, the wheel hash bit-for-bit, and the
release-gate accounting from its own JUnit records, and confirmed by git history that no
supplied specification file had been modified. Its 83 adversarial tests then found four
API-reachable defects, all repaired in `7db8a6c`:

- Completion could record a substantive finding after failed, lost or cancelled
  execution, accepted evidence references that were not content addresses, and could
  declare a valid protocol with no verified succeeded attempt.
- A report checked that cited evidence was valid and independently recomputed but not
  that it belonged to the citing campaign, so another campaign's comparison artifact
  could be presented as certified evidence and support a quantitative claim.
- Memory retrieval recorded the requesting project without consulting it, so a
  source-project grant followed its principal into an unrelated project.
- The shared typed result boundary accepted `source="live"`, and the receipt-driven
  recovery path did not re-check a stored output's provenance against its frozen plan.

- Independent audit report for `e03b73f`: `/private/tmp/claude-501/-Users-iamsikun-research-kestrel/64641c09-5604-48b7-82aa-019781b57bb9/scratchpad/review/REPORT.md`, SHA256 `b8ab1bbd4a9d015b4de1590493bab9d82bce38df6a06c1e18bcd13e604ca4247`.

That auditor wrote the `7db8a6c` repairs and is therefore an author of this revision,
not an independent review of it. The completion, evidence-attribution and
provider-labelling repairs are trust-boundary changes and require a further independent
review by a reviewer who did not write them. The audit also recorded, and this document
confirms, two limits that are not defects: a forged JUnit record can declare full
readiness, because the gate report is evidence bookkeeping and not operator authority;
and the container profile pins no seccomp or AppArmor profile and uses no user-namespace
remapping, so the tested Docker Desktop VM measurements differ from a native Linux target
host.

Preliminary reports for `a75fb83` and `949c820` remain external historical evidence;
they do not certify this revision. Coverage review exposed missing A05/A08 mappings,
parser-only A29 evidence, static-only conformance, and simulated-only A38 progress.
The current suite tests the repaired paths, including cancellation when an agent
plan is malformed, invalidated, deleted, tampered or restricted. A cancellation
receipt explicitly declines to verify unavailable input content.

The smallest next input for A28 is a provenance-bearing pair of sanitized,
public-synthetic Codex/Claude structured captures with actual provider versions,
matching task context and SHA256 values, supplied in the fixture format described
by `tests/test_provider_records.py`. Existing captures can be checked offline via
`KESTREL_PUBLIC_PROVIDER_CAPTURES`; new live capture generation requires separate
explicit authorization. Synthetic documentation examples cannot replace them.

Before any unattended deployment, a separate operator must review the tested
wheel and current source, audit the target Linux service identity, credential and
mount boundaries, and explicitly authorize deployment. A30/A31 additionally need
an approved provider/data policy and bounded call/token budget, implementation and
test of the actual provider credential boundary, then a working version pin. A43
requires actual authorized target GPU validation. No such authority is created by
this build, its operator-token demonstrations, this document or a passing test.
