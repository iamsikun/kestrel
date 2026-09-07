# Verification of the first pilot

The tested implementation revision is `2157914e776b42112e00ec3a760eec7fc024022a`
on `build/first-pilot`. Verification completed on 2026-09-06 local time
(2026-09-07 UTC). Subsequent documentation commits do not change this tested code.
The implementation is functional, but the complete acceptance gate remains blocked.
No deployment, publication, private research access, live provider call, cloud job,
or host-service/driver modification was performed.

## Results and acceptance accounting

All runtime suites ran serially against that committed source; the source tree
was clean. Tests generated external synthetic projects and labs. No user research
repository was read or used. Tests and fixture campaigns made no external network
requests after setup; isolated egress checks used local test endpoints.

| Check | Result | Exit status |
|---|---|---|
| Core unit, integration, adversarial and Hypothesis state-machine suite | 230 passed, 1 skipped, 12 deselected | 0 |
| Fresh external offline wheel install and installed CLI demo | 1 passed | 0 |
| Actual Docker Linux VM isolation suite | 11 passed, 19 deselected | 0 |
| Ruff | Passed | 0 |
| Original specification integrity | 20 files, 46 requirements unchanged | 0 |
| Offline source distribution and wheel build | Passed | 0 |
| Release-gate report | 42 requirements passed, 4 blocked | 1, expected |

The core skip is A28: actual sanitized Codex and Claude captures are unavailable.
The 12 deselections in the core command are the separately executed install and
isolation tests; they are not skipped deployment evidence. The isolation command
selects 11 container tests and deselects 19 already covered development tests.
Core duration was 16.04 seconds; install 4.00 seconds; isolation 18.66 seconds.
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
are retained in `install-evidence.json`; copied logs are in `install-logs/`.

The exact final suite commands used these environment values:

```sh
export KESTREL_SOURCE_REVISION=2157914e776b42112e00ec3a760eec7fc024022a
KESTREL_TEST_SCOPE=core .venv/bin/python -m pytest -m 'not isolation and not install' --junitxml=/private/tmp/kestrel-verification-2157914/core.xml -q
uv build --offline
KESTREL_TEST_SCOPE=install KESTREL_WHEEL=/Users/iamsikun/research/kestrel/dist/kestrel_research_runtime-0.1.0-py3-none-any.whl KESTREL_WHEELHOUSE=/private/tmp/kestrel-wheelhouse KESTREL_INSTALL_EVIDENCE=/private/tmp/kestrel-verification-2157914/install-evidence.json .venv/bin/python -m pytest tests/test_install.py -q --junitxml=/private/tmp/kestrel-verification-2157914/install.xml
KESTREL_TEST_SCOPE=isolation KESTREL_TEST_PROFILE=isolated-local KESTREL_TEST_RUNTIME=linux-docker-vm KESTREL_RUN_ISOLATION=1 KESTREL_DOCKER_IMAGE=sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca .venv/bin/python -m pytest tests/test_runners.py -m isolation -q --junitxml=/private/tmp/kestrel-verification-2157914/isolation.xml
.venv/bin/ruff check src tests tools/release_gates.py tools/prepare_wheelhouse.py
python3 tools/validate_pack.py
```

Do not set a previous source revision when testing changed code. The fixture
supervisor needs permission to inspect its own process tree; when the development
tool sandbox denied that earlier, the attempt stayed uncertain and retained its
reservation. That run was not treated as a successful execution gate.

To reproduce the acceptance calculation from the final records:

```sh
.venv/bin/python tools/release_gates.py --junit /private/tmp/kestrel-verification-2157914/core.xml --junit /private/tmp/kestrel-verification-2157914/install.xml --junit /private/tmp/kestrel-verification-2157914/isolation.xml --source-revision 2157914e776b42112e00ec3a760eec7fc024022a --output /private/tmp/kestrel-verification-2157914/recomputed-gates.json --blocker 'A28=Actual sanitized provider captures unavailable' --blocker 'A30=Live provider test unauthorized and untested' --blocker 'A31=Credential-boundary integration unauthorized and untested' --blocker 'A43=Target GPU untested'
```

This command intentionally exits 1 while A28 is blocked. The retained original
report additionally fingerprints the wheel, build checks, install evidence, demo,
both evidence packets and the earlier independent-review reports.

## Demo evidence and hashes

Evidence directory: `/private/tmp/kestrel-verification-2157914/`. Runtime data are
external to the framework checkout. These local temporary files may be removed
by normal system cleanup; preserve them externally if long-term retention is needed.

The clean-installed demo completed two campaigns with six charged attempts: two
finite offline agent tasks and four real fixture worker executions. All stopped
and released their reservations. No provider calls or tokens were consumed.
Numerical baseline/treatment errors were 0 and 4; counterexample counts were 0 and
2. Both valid findings are `not_supported`. This is exhaustive finite synthetic
verification, with no population inference or claim about model intelligence.

`demo.json` retains the full reports. The default demo exports its last campaign;
`numerical-evidence.zip` was separately exported through the same API, so both
campaigns have standalone packets. Exports are scoped by campaign provenance and
exclude operator tokens and unrelated artifacts. Imports retain imported assurance.

Wheel SHA256: `212f7f1bb9a00f9148332654f2de3d4a06f620d1aa3b1f065d29a0e24b57f0ab`.

| Evidence file in the directory above | SHA256 |
|---|---|
| `core.xml` | `eae96df10c0bed5dfb739500b0d074caaba95279ac52dbd07e07a01f63ee9510` |
| `install.xml` | `f488927462e351fcbe2ef8d260d31464da15087709b14ecc73c7b9efb3c2e56d` |
| `isolation.xml` | `cb67cd0299beec0e8a2a6b9e7b3277a87ffc4955dfe7f297e8bf448d2705c7ee` |
| `install-evidence.json` | `813166d289bb2f4eff0c115ae32cbb133db248243eb2b206f67dd7dfbdb380ab` |
| `demo.json` | `297516cbfd62a9f24c15c9cc11451ba65d909ed8b42a6cc971a9f9f9156e6f6d` |
| `numerical-evidence.zip` | `6b786ffac84c7747ed50becd3a67efbb76947ba4bb28b2f6e875845965e93ee0` |
| `counterexample-evidence.zip` | `7de4956be11062ffea428020ca60f35f90aa4d9ad1825d82b7c09ed0b304755e` |
| `release-gates.json` | `d938f3baa954da308dcdbc03a2adc6964d939b6f1540957855bc6b000f71562d` |

`sha256.json` lists all final record hashes. Hashes establish content identity,
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
