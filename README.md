# Kestrel

Kestrel runs bounded research experiments and records the evidence behind their
results. **The current release is an offline developer pilot:** you can run two
synthetic experiments, inspect their attempts and findings, and export their
evidence. It uses a deterministic mock coding agent.

**You cannot yet give it an arbitrary research task or connect a live Codex/Claude
agent.** Registering your own project does not enable running it. General project
execution, live providers, and the broader personal assistant loop are unfinished.

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

## Where to go next

| Document | Use it for |
|---|---|
| [Usage](docs/USAGE.md) | Manual fixture campaigns, recovery, and verification commands |
| [Repository guide](docs/REPOSITORY_GUIDE.md) | Code map, runtime layout, and capability gaps |
| [Build state](BUILD_STATE.md) | Current evidence and remaining blockers |
| [Provider status](docs/PROVIDER_STATUS.md) | What mock/replay supports and why live agents are unavailable |
| [Product design](docs/PRODUCT.md) | The intended personal research assistant |
| [Security](docs/SECURITY.md) and [science](docs/SCIENCE.md) | Execution boundaries and rules for scientific conclusions |

The original build-kit README is preserved at
[docs/specification/README.md](docs/specification/README.md). Original design
documents and `examples/` describe intended interfaces, some of which remain
unimplemented. `tools/validate_pack.py` checks the original specification payloads;
its `framework_implemented: false` field is not a check of today's runtime.
