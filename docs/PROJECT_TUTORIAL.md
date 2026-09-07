# Two synthetic projects, from onboarding to evidence

Use a new temporary directory outside Kestrel. Supply an already available public
Python 3 image as `IMAGE_NAME@sha256:DIGEST`. Kestrel never pulls it automatically.
The generator creates an arithmetic program and a string repetition program,
with different operations and worker adapters wrapping ordinary commands:

```sh
python -m kestrel.integration_examples /tmp/kestrel-example-sources --image IMAGE_NAME@sha256:DIGEST
kestrel lab init /tmp/kestrel-example-lab
kestrel --lab /tmp/kestrel-example-lab project add /tmp/kestrel-example-sources/arithmetic
kestrel --lab /tmp/kestrel-example-lab project add /tmp/kestrel-example-sources/strings
kestrel --lab /tmp/kestrel-example-lab project list
kestrel --lab /tmp/kestrel-example-lab project inspect arithmetic --json
```

For an ordinary project instead, `kestrel project init PATH` creates an incomplete
`.kestrel` directory exclusively. Fill purpose, references, operations, parameter
schemas, explicit source inclusion, editable/protected paths, classification and
image. Implement the adapter. The bundled `kestrel.adapter` helper can be copied
into `.kestrel/helper.py`; it requires only Python's standard library. It runs
only inside a worker. The examples show a complete wrapper and contract.

Preview arithmetic's source, then pass its `selection_digest` to create it:

```sh
kestrel --lab /tmp/kestrel-example-lab project snapshot arithmetic --preview --json
kestrel --lab /tmp/kestrel-example-lab project snapshot arithmetic --expect DIGEST --json
kestrel --lab /tmp/kestrel-example-lab experiment propose --snapshot DIGEST --operation calculate --parameters '{"value":5}' --json
```

The default program multiplies by two. For a bounded source edit, create a JSON
file containing a list with `path: "program.py"`, `original: SHA256_OF_ORIGINAL`,
and `content: COMPLETE_REPLACEMENT_TEXT`. Use the hash from the preview and change
`FACTOR = 2` to `FACTOR = 4` in the replacement. Pass `--edits /tmp/edits.json`
on a second proposal. The second experiment will report 20 for value 5. Its patch
and snapshot contents are retained in the evidence export. Nothing edits the
original program. Unknown parameters, values outside 1–10, stale original hashes,
and changes to `.kestrel` or declared references are rejected before dispatch.

Inspect the proposal and approve its exact digest through the operator path:

```sh
kestrel --lab /tmp/kestrel-example-lab experiment inspect EXPERIMENT
kestrel --lab /tmp/kestrel-example-lab experiment approve EXPERIMENT --digest CONTRACT_DIGEST --operator-token-file /tmp/kestrel-example-lab/operator.token --json
kestrel --lab /tmp/kestrel-example-lab experiment run EXPERIMENT --approval APPROVAL
kestrel --lab /tmp/kestrel-example-lab experiment status EXPERIMENT --json
kestrel --lab /tmp/kestrel-example-lab experiment artifacts EXPERIMENT --json
kestrel --lab /tmp/kestrel-example-lab experiment export EXPERIMENT --output /tmp/experiment.zip
```

Repeat snapshot and proposal for `strings`, operation `repeat`, parameter value 5.
Its JSON artifact reports `samplesamplesamplesamplesample`. JSON stdin carries
attempt identity and parameters; stdout contains one bounded protocol response;
logs go to stderr. The controller recalculates artifact hashes and records their
attempt attribution. A valid response and a zero exit code are both required.
Project metrics are self-reported, and scientific outcome is `not_evaluated`.

After an intentional integration contract change, run `project refresh ID` then
preview/snapshot again. Historical proposals keep the old revision and snapshot.
Source-only changes require a new preview/snapshot, not a contract refresh.
`experiment cancel ID` requests a confirmed stop; rerunning `experiment run` after
an interruption reconciles the same charged attempt before any launch. Uncertain
jobs retain capacity. No retry is silently created.

Common setup failures:

- Existing `.kestrel`: init refuses to overwrite; inspect and complete its files.
- Incomplete template: validation identifies missing fields or invalid values.
- Duplicate source/name: choose an unused name for a distinct source with `--name`.
- Changed preview: inspect the new selection and pass the new digest explicitly.
- Selected symlink, FIFO, nested repository or LFS pointer: resolve/remove it or
  explicitly exclude supported non-source content; never silently dereference.
- Missing pinned image or Linux resource capability: execution fails before launch.
- Hard workspace quota requested: unavailable, so launch is refused.
- Dependency missing in image: execution fails; arrange a separately prepared image.

The public local API uses the same services:

```python
from kestrel.client import Client
with Client('/tmp/kestrel-example-lab') as client:
    project = client.describe_project('arithmetic')
    preview = client.preview_snapshot('arithmetic')
    snapshot = client.projects.snapshot('arithmetic', preview['selection_digest'])
    proposal = client.propose_experiment(snapshot['digest'], 'calculate', {'value': 5})
    status = client.status(proposal['experiment_id'])
```

This API has inspect, run, cancel and artifact_list methods. It does not provide an
HTTP service, a live agent or deployment authority. CLI failures return exit 2 and
JSON `error`/`message`; successful `--json` output is the service's structured data.
