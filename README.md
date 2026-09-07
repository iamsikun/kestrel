# Kestrel

Kestrel is a local research runtime with an offline synthetic campaign pilot and
an existing-project integration workflow. It keeps original repositories separate
from experiment workspaces and records approvals, attempts, budgets and evidence.

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

See the [synthetic onboarding tutorial](docs/PROJECT_TUTORIAL.md),
[selected-source semantics](docs/PROJECT_INTEGRATION.md),
[legacy usage](docs/USAGE.md), [build state](BUILD_STATE.md), and
[decisions](docs/DECISIONS.md). `python3 tools/validate.py` checks specification
integrity only; runtime verification requires the test suites.

The [original specification README](docs/specification/README.md) is preserved
byte-for-byte. Its relative paths refer to the repository root; use the
[archive navigation guide](docs/specification/INDEX.md) to follow them.
