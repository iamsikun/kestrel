# Running the implemented pilot

The original README and build specifications are preserved byte-for-byte. The
implementation is a developer pilot; read `BUILD_STATE.md` for the latest evidence
and remaining gates. Registering an arbitrary project does not grant permission
to execute it. The developer CLI executes only exact generated fixture programs.

## Setup and offline demo

From this checkout, with Python 3.12 or later and uv installed:

```sh
uv sync --locked --python 3.13
uv run --offline kestrel doctor
uv run --offline kestrel demo --offline
```

The demo creates an external temporary lab, two independent Git repositories,
two frozen campaigns and four real subprocess attempts. It preserves the original
brief and source, recomputes numerical error and finite integer counterexamples,
rejects worker-supplied favorable metrics, and completes with two `not_supported`
findings. The JSON output identifies the retained temporary directory and portable
evidence ZIP. Neither a model account nor Docker is needed for this developer demo.
Use `--output /absolute/new/external/lab` to choose its location; the path must not exist.

The fixture Python processes reject socket events. This supports deterministic
offline testing of trusted code; it is not an OS security boundary. Process-state
inspection must be permitted so the supervisor can confirm entire job termination.
If that check is unavailable, attempts remain uncertain and resources stay reserved.

## CLI adoption flow

All lab/runtime/project paths must stay outside the framework. Sidecars contain
data and argument vectors; registration never runs Git, imports, setup hooks or
project programs. Complete explicit snapshots include ignored/untracked files and
empty directories. `.git` metadata is accounted as excluded, and unresolved Git
revisions, submodules, LFS pointers, special files, symlinks and hardlinks fail closed.

```sh
kestrel lab init /absolute/new/lab
kestrel --lab /absolute/new/lab project register --manifest /external/project.sidecar.json --snapshot-dirty
kestrel --lab /absolute/new/lab project validate PROJECT_ID
kestrel --lab /absolute/new/lab campaign propose --project PROJECT_ID --brief /external/brief.txt
kestrel --lab /absolute/new/lab campaign inspect CAMPAIGN_ID
kestrel --lab /absolute/new/lab campaign approve CAMPAIGN_ID --digest EXACT_DIGEST --operator-token-file /absolute/new/lab/operator.token
kestrel --lab /absolute/new/lab campaign run CAMPAIGN_ID --approval APPROVAL_ID
kestrel --lab /absolute/new/lab campaign status CAMPAIGN_ID
kestrel --lab /absolute/new/lab campaign report CAMPAIGN_ID
kestrel --lab /absolute/new/lab evidence export CAMPAIGN_ID --output /external/packet.zip
```

Campaign proposal currently supplies the built-in finite numerical/counterexample
protocol for generated fixture sources. General sidecar registration, the typed
controller and Docker driver are usable components; a general isolated campaign
application and separately operated deployment are not enabled by this CLI.

Approval requires the developer lab's generated operator token, and binds the
contract digest, principal, policy and expiry. This tests authority logic in a
user-owned developer lab; it does not establish a separate deployed identity.
The token is never put in job environments or evidence exports. The application
has no deployment or live-provider authority operation.

Repeated `campaign run` reconciles stable attempts before proceeding. A crash
after dispatch or verification must not allocate a duplicate computation. An
uncertain job holds capacity until inspection can resolve it. Never delete job
journals to make a stuck attempt retry; retain evidence and investigate the driver.
`campaign cancel CAMPAIGN_ID` cancels queued work and asks the driver to stop jobs;
capacity is retained until stop confirmation.

Evidence imports always retain `imported` assurance. Reports consult stored trust
and invalidation status; hashes alone cannot certify a scientific claim. Failed
outputs remain diagnostics, separate from successful observations. Cache reuse
is not an independent replicate. These are finite synthetic checks, not general
scientific inference or proof of security.

## Reproduce verification

Development setup can contact the package registry. Tests and campaigns use no
network after setup. Run runtime test suites sequentially so independent test labs
do not exceed the intended fixture concurrency envelope.

```sh
.venv/bin/ruff check src tests tools/release_gates.py tools/prepare_wheelhouse.py
.venv/bin/python -m pytest -m 'not isolation and not install' -q
uv build --offline
.venv/bin/python tools/prepare_wheelhouse.py --output /private/tmp/kestrel-wheelhouse --download
```

The wheelhouse helper downloads compatible locked runtime wheels from the approved
registry and verifies every SHA256. It runs no dependency build hooks. For the
clean-install gate, set `KESTREL_WHEEL` to the absolute built wheel and
`KESTREL_WHEELHOUSE` to that external directory, then run:

```sh
.venv/bin/python -m pytest tests/test_install.py -q
```

The test creates a fresh external environment and empty installer cache, installs
offline with no index/builds, verifies imports, and executes the installed console
entry point outside this checkout. Missing wheel setup is a skipped, unsatisfied gate.

To measure the container profile, select an already installed public Python image
by exact local `sha256:...` ID. There is no implicit image pull, host-service change
or GPU setup. Set `KESTREL_RUN_ISOLATION=1`, `KESTREL_DOCKER_IMAGE=sha256:...`,
`KESTREL_TEST_SCOPE=isolation`, `KESTREL_TEST_PROFILE=isolated-local`, and
`KESTREL_TEST_RUNTIME=linux-docker-vm` (or the actual target runtime), then run:

```sh
.venv/bin/python -m pytest tests/test_runners.py -m isolation -q
```

The Docker tests inspect actual forbidden accesses and egress, resource saturation,
whole-job cancellation, restart and deadline attacks. Workspace storage uses an
advisory watchdog; requested hard writable-workspace quotas are rejected. The
read-only container filesystem and capped tmpfs have separately tested behavior.
The external supervisor requires a responsive daemon to confirm termination;
daemon outages retain uncertainty. Docker Desktop Linux VM results do not replace
a target Linux service-identity audit.

For release accounting, set `KESTREL_SOURCE_REVISION` to the exact tested commit and
`KESTREL_TEST_SCOPE` to `core`, `install` or `isolation`; add `--junitxml=/external/result.xml`
to each test command. Feed the resulting XML files to `tools/release_gates.py` with
`--junit` repeated, `--source-revision`, and `--output`. Missing/skipped/failed checks
cannot inherit older green results. See `docs/PROVIDER_STATUS.md` for provider gates.

No push, publication, controller deployment, private project enrollment, paid
provider call, cloud job or host-service/driver change is part of these commands.
