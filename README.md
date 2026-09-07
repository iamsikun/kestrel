# Kestrel

Kestrel runs bounded research experiments and records the evidence behind their
results. The offline developer demo runs two synthetic scientific comparisons
using a deterministic mock coding agent. The project-integration workflow runs
explicitly proposed operations and bounded source edits in isolated copies of
connected synthetic projects.

You cannot yet give it an arbitrary research brief or connect a live Codex/Claude
agent. Registration does not grant execution permission: project experiments need
an exact operator approval and an available digest-pinned Linux Docker image.
The broader personal assistant loop remains unfinished.

## Run it now

Prerequisites: Git and `uv` on your PATH. The setup below selects Python 3.13 and
installs the locked dependencies; setup may need network access. No model account,
API key, Docker, or GPU is needed for this demo.

Open a terminal in this repository and run:

```sh
uv sync --locked --python 3.13
uv run --offline kestrel doctor
uv run --offline kestrel demo --offline --output /tmp/kestrel-first-lab
```

`/tmp/kestrel-first-lab` must be a **new directory outside this checkout**. If it
already exists, choose another name. To let Kestrel choose a fresh temporary
directory automatically, omit `--output /tmp/kestrel-first-lab`.

`doctor` describes the available developer profile and blocked features. It does
not run acceptance tests. The demo initializes a lab, creates two synthetic Git
projects, approves its fixed campaigns using the lab's local developer token,
runs a mock-agent task and two worker attempts per campaign, and prints JSON.
The demo handles these steps automatically; it does not prompt you for a question.

Expected findings:

| Experiment | Baseline | Candidate | Finding |
|---|---|---|---|
| Numerical prediction | Mean squared error 0 | Mean squared error 4 | `not_supported` |
| Integer counterexample search | 0 counterexamples | 2 counterexamples | `not_supported` |

`not_supported` is the expected successful result: the proposed improvements are
deliberately worse. Each report should show `state: COMPLETE`,
`execution: succeeded`, `validity: valid`, and `assurance: independently_recomputed`.
There are six attempts across the two campaigns and zero provider calls.

## Inspect and export a result

Continue in the same repository. These commands read the first campaign ID from
the saved demo output, so there are no IDs to invent or copy manually:

```sh
KESTREL_LAB=/tmp/kestrel-first-lab
KESTREL_CAMPAIGN=$(uv run --offline python -c 'import json,sys; print(json.load(open(sys.argv[1]))["reports"][0]["campaign_id"])' "$KESTREL_LAB/demo.json")

uv run --offline kestrel --lab "$KESTREL_LAB" campaign inspect "$KESTREL_CAMPAIGN"
uv run --offline kestrel --lab "$KESTREL_LAB" campaign report "$KESTREL_CAMPAIGN"
uv run --offline kestrel --lab "$KESTREL_LAB" evidence export "$KESTREL_CAMPAIGN" --output "$KESTREL_LAB/numerical-evidence.zip"
```

If you chose another demo directory, change `KESTREL_LAB` accordingly.
`inspect` shows the frozen question, candidate recipes, controls, and budget.
`report` shows the outcome, every attempt, and the evidence references.
`numerical-evidence.zip` contains the first campaign's portable evidence packet.
Use a fresh export filename if that file already exists.

The lab also retains:

- `demo.json`: reports for both campaigns and the resolved lab path.
- `evidence-packet`: a ZIP containing the demo's second campaign, despite having
  no `.zip` extension.
- `runtime/`: execution records, candidate workspaces, and artifact storage.
- `definition/`: generated project registrations.
- `operator.token`: the local approval credential; do not share it with workers
  or include it in evidence packets.

Temporary directories may be cleaned by the operating system. For results you
want to retain, choose a new persistent lab directory outside this checkout when
running the demo.

## Run campaigns manually

The CLI also exposes `lab init`, `project register`, `campaign propose`, `approve`,
`run`, `status`, and `cancel`. The [manual walkthrough](docs/USAGE.md#manual-fixture-campaign)
starts from the generated demo project and explains exactly where each ID comes
from. A new brief still uses the built-in fixture comparison; its wording does
not generate a new experiment design.

For interrupted work, rerun the same `campaign run` command with its existing
campaign and approval IDs. Kestrel reconciles existing jobs before proceeding.
If it reports that a job remains uncertain, it could not confirm termination.
On macOS, a restricted execution environment may block the process inspection it
needs; run this trusted demo from an ordinary terminal. Preserve the lab and job
journals rather than deleting them to force a retry. The development driver is
for known fixtures and provides no adversarial isolation for arbitrary code.

Command help:

```sh
uv run --offline kestrel --help
uv run --offline kestrel campaign --help
uv run --offline kestrel campaign approve --help
```

## Connect projects and run isolated experiments

Install the built wheel in a Python 3.12+ environment:

```sh
uv build --offline
uv tool install dist/kestrel_research_runtime-0.1.0-py3-none-any.whl
kestrel lab init /absolute/path/to/lab
kestrel doctor
```

Normal development dependency setup uses `uv sync --locked`. Experiments never
install dependencies or pull images. The worker image must already contain Python
3 and the project's dependencies, and be pinned by digest.

```sh
kestrel project init /absolute/path/to/project
# Complete .kestrel/project.yaml and .kestrel/adapter.py.
kestrel --lab /absolute/path/to/lab project add /absolute/path/to/project
kestrel --lab /absolute/path/to/lab project snapshot project --preview --json
kestrel --lab /absolute/path/to/lab project snapshot project --expect SELECTION_DIGEST --json
kestrel --lab /absolute/path/to/lab experiment propose --snapshot SNAPSHOT_DIGEST --operation OPERATION --parameters '{"value":5}' --json
kestrel --lab /absolute/path/to/lab experiment inspect EXPERIMENT_ID
kestrel --lab /absolute/path/to/lab experiment approve EXPERIMENT_ID --digest CONTRACT_DIGEST --operator-token-file /absolute/path/to/lab/operator.token --json
kestrel --lab /absolute/path/to/lab experiment run EXPERIMENT_ID --approval APPROVAL_ID
kestrel --lab /absolute/path/to/lab experiment artifacts EXPERIMENT_ID
kestrel --lab /absolute/path/to/lab experiment export EXPERIMENT_ID --output /absolute/path/to/packet.zip
```

Only synthetic public experiments are enabled in this construction profile.
Operator approval is separate from project configuration. Source references are
untrusted information. No command applies candidate edits to original sources.

| Capability | Availability |
|---|---|
| Static onboarding, explicit selection, revision history | Python API and CLI |
| Parameters and bounded source replacements | Validated before dispatch |
| Execution-only experiments | Existing digest-pinned Linux Docker image required |
| Offline scientific fixture demo | `kestrel demo --offline` |
| Legacy sidecar registration | `project register --manifest ... --snapshot-dirty` |
| Read-only briefings | `kestrel --lab LAB brief` |
| Live model, GPU, remote service, unattended deployment | Not enabled/certified |

Containers use network none, read-only root and CPU/RAM/PID limits. Writable
workspace storage uses an advisory watchdog; requests for a hard quota fail.
Docker Desktop exercises a Linux VM and does not certify unattended native Linux
use. Missing isolation infrastructure is an unmet verification gate.
Execution-only artifacts are protocol checked and self-reported; they establish
no scientific finding. The offline fixture evaluator is a separate workflow.

## Where to go next

| Document | Use it for |
|---|---|
| [Project tutorial](docs/PROJECT_TUTORIAL.md) | Two-project onboarding, parameters, bounded edits, and isolated execution |
| [Source selection](docs/PROJECT_INTEGRATION.md) | Snapshot rules and integrity semantics |
| [Usage](docs/USAGE.md) | Manual fixture campaigns, recovery, and verification commands |
| [Repository guide](docs/REPOSITORY_GUIDE.md) | Code map, runtime layout, and capability gaps |
| [Build state](BUILD_STATE.md) | Current evidence and remaining blockers |
| [Provider status](docs/PROVIDER_STATUS.md) | What mock/replay supports and why live agents are unavailable |
| [Product design](docs/PRODUCT.md) | The intended personal research assistant |
| [Security](docs/SECURITY.md) and [science](docs/SCIENCE.md) | Execution boundaries and rules for scientific conclusions |

The [original specification README](docs/specification/README.md) is preserved
byte-for-byte. Its relative paths refer to the repository root; use the
[archive navigation guide](docs/specification/INDEX.md) to follow them.

`python3 tools/validate.py` checks specification integrity and explicitly reports
that runtime verification was not run. The original `tools/validate_pack.py` is
also preserved; its fixed `framework_implemented: false` field describes the kit,
not the current runtime.
