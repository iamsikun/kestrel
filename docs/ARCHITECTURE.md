# Architecture

## 1. Overall model

Think of Kestrel as a research compiler plus an execution and evidence runtime:

```text
brief + existing evidence
        |
        v
planner -> proposed campaign contract and task DAG
        |
        v
schema + policy + scientific-protocol validation
        |
        v
controller -> job driver -> isolated workspaces
        |                       |
        |                       v
        |               immutable result artifacts
        v                       |
independent evaluator <---------+
        |
        v
analysis + evidence graph -> decision packet -> next proposed action
```

The planner is replaceable and non-authoritative. The controller is ordinary software. The evaluator is an explicitly registered, versioned instrument; “trusted” means protected and approved, not scientifically infallible.

## 2. Initial implementation stack

Use a modular Python package with strict schemas, a CLI, and a single-host controller. A reasonable starting stack is Python 3.12, Pydantic v2, Typer, PyYAML safe loading, SQLite, standard-library asyncio/subprocess, and ordinary filesystem artifacts. Pin working dependency versions with uv. Core tests use pytest and Hypothesis. Current APIs are confirmed during implementation.

Use a controller transaction to update state, append an event, and reserve resources. Store task results as artifacts; do not put giant model responses, checkpoints, or arrays in the DB. Start with explicit SQL migrations or a small established migration layer; do not write a general ORM or event-sourcing framework.

Do not put another agent framework underneath every component. Codex/Claude supply coding-agent loops. Kestrel supplies research semantics and authority. LangGraph may later help complex reasoning subflows; Temporal may replace the single-host job engine when genuinely distributed durable execution is needed. Neither makes arbitrary external side effects exactly-once by itself. [S1-S5]

Use one tested container backend first, behind a JobDriver protocol. A plain-process driver is useful for trusted fixtures and development only. Production resource and isolation capabilities must be probed and advertised, not assumed from the backend name.

## 3. Suggested module boundaries

```text
src/kestrel/
  contracts/         # typed canonical objects and versioning
  application/       # use cases, no provider-specific SDK imports
  controller/        # state transitions, reservations, recovery
  policy/            # evaluation of deployed capability policies
  projects/          # registry and immutable source resolution
  adapters/          # project protocol, protocol validator
  agents/            # mock, Codex, Claude adapters
  runners/           # job-driver interface, process-fixture, container
  evidence/          # artifact ingestion, provenance, claims, invalidation
  evaluation/        # evaluator registry and analysis job orchestration
  interfaces/        # CLI, later authenticated API and optional MCP
  observability/     # structured logs, metrics, redaction
```

Do not pre-create empty abstractions for all modules. A component should be introduced in the first vertical slice that uses it. Prevent imports from controller/ into provider SDKs or external project code. Provider-specific work belongs behind adapters.

## 4. Core records

- **ProjectRef**: external source locator, permitted source roots, adapter identity, capability declarations, data classification. No source tree embedded in the record.
- **Brief**: original question, priorities, known evidence and exclusions. Preserve original text and interpreted assumptions.
- **CampaignContract**: question, estimand or target claim, baseline, allowed interventions, controls, search policy, evaluation/analysis identity, resource envelope, authorized capabilities, completion/stopping rules.
- **Candidate**: one immutable implementation/configuration proposal relative to a baseline; may be a configuration-only change, an extension, or a core fork.
- **TaskSpec**: bounded action with input artifact identities, output schema, dependencies, authority requirements, driver, retry policy, timeout, resources, and acceptance checks.
- **Attempt**: actual execution of a task. Unique identity and complete status history; retries are new attempts, not overwritten runs.
- **Artifact**: digest, media type, size, producer, input lineage, data classification, locations, completeness and retention status.
- **Observation**: evaluator-produced measurement or proof-check result, tied to exact inputs and method.
- **Claim**: scoped interpretation citing observations, assumptions, analysis, uncertainty and contrary evidence.
- **Approval**: operator/service-authorized capability grant tied to a contract digest, policy version, scope, expiry, and permitted principals.
- **Decision**: continue, replicate, stop, invalidate, amend contract, request authority, or promote reusable code.

Use explicit schemas and schema versions. Unknown critical fields fail. Arbitrary Python object deserialization is forbidden at boundaries. JSON/YAML manifests are data, not executable templates.

## 5. Identity and reuse

A recipe identity hashes canonical behavior-affecting inputs: source snapshot, full resolved config, environment/image identity, input data/tokenizer artifacts, seed plan, evaluator/analysis versions, and protocol version. Do not use file modification time or mutable branch names as experiment identity.

An attempt ID is different from a recipe ID. The same recipe may be executed for replication, recovery, or diagnostics. A reused cached result is never counted as an independent replication. New independent replicates require their declared randomization units and identities, not just an output-directory change.

Include hardware/runtime information in observations and in cache eligibility when it matters. Reproducibility levels are explicit: provenance replay, tolerance-based numerical reproduction, or bitwise continuation under a specified environment. LLM API re-execution is not guaranteed to reproduce the same plan; recorded outputs can be replayed as historical artifacts.

## 6. External workspace strategy

Resolve each candidate's input code to an immutable source bundle or a commit plus verified content digest. Reject dirty sources by default; an explicit snapshot operation may capture tracked changes, selected untracked files, submodules, and large-file references with complete accounting. Do not silently drop them.

Workers receive independent workspaces. They cannot edit the original project, another candidate, the controller, or a mutable shared environment. Read-only content-addressed caches may be shared after verification. Mutable caches and outputs are attempt-local. Controller-managed snapshots must remain outside the framework source tree.

Git worktrees can be a development optimization, but shared Git administrative state must not become worker authority. Independent snapshots are the simplest default. Main branches are never automatic baselines. Promotion creates a new baseline identity without changing old campaigns.

## 7. Durable execution

Keep workflow-task state separate from scientific outcome:

```text
Task: PLANNED -> VALIDATED -> QUEUED -> STARTING -> RUNNING -> VERIFYING
                                                              |
                                                      SUCCEEDED / FAILED
Additional states: BLOCKED, CANCELLED, RECOVERING, LOST

Campaign: EXPLORING -> FROZEN -> CONFIRMING -> COMPLETE
Finding: SUPPORTED_IN_SCOPE / NOT_SUPPORTED / INCONCLUSIVE / INVALID
```

Specify legal transitions in a table and test them as a state machine. Terminal records remain in history. “Job exited zero” does not imply valid results or supported hypothesis.

Use transactional dispatch with a stable attempt ID and backend job label. If the controller crashes between dispatch and recording completion, it must reconcile by that identity before retrying. Never promise exactly-once execution for arbitrary external code. Require idempotent launch/reconciliation and identify any residual uncertainty.

Reserve CPU/GPU slots, memory allowances, storage allowances, and campaign budget before dispatch. Use monotonic time for local deadlines and UTC timestamps for audit records. Leases require heartbeats and fencing/version checks. Lease expiry alone must not start duplicate computation; confirm that the old process/container is stopped or mark the resource uncertain.

A driver exposes launch, inspect, cancel, reconcile, and capability probing. Cancellation must terminate the entire job process tree/container and confirm it is stopped before releasing exclusive resources. Graceful checkpoint requests are optional project capabilities; the controller cannot synthesize exact checkpoint semantics for an arbitrary application.

## 8. Evidence and invalidation

Use ordinary relational records for the first evidence graph. Nodes and edges can be tables; no graph database is necessary initially.

```text
claim -> analysis -> observation -> evaluator + result artifacts
      -> contract -> baseline + candidate + inputs + execution attempts
```

Hashes establish content identity, not authenticity or truth. A trusted ingester records who produced an artifact and which verified attempt it came from. Protect the store from worker mutation. A local hash chain is useful for detecting some changes but is not tamper-proof against an administrator who controls all records.

If an evaluator or dataset is discovered to be invalid, add an invalidation event and propagate a stale/invalid label to dependent observations, comparisons, and claims. Do not silently delete historical evidence or continue displaying a green claim with invalid inputs.

Export a portable evidence bundle containing manifests, checksums, provenance, available input references, approved source artifacts, evaluator identity, command recipes, observations, report, and limitations. Restricted data may be omitted with an explicit reproduction limitation. Import does not promote external evidence to independently verified status.

## 9. Agent roles and context

Initially use three role templates, not three permanent daemon agents:

- Planner: interpret the question, consult approved sources/evidence, propose discriminating experiments and a task graph.
- Builder: implement one bounded candidate and its development tests.
- Critic: independently challenge assumptions, compare with references, design counterexamples and evaluate whether checks actually test the claim.

The same provider may serve different roles in separate sessions. Different model brands are not sufficient independence. The critic must see the original contract and raw artifacts, not only the builder's summary. When a reviewer edits code, it becomes an author of that revision and cannot be the sole independent review of that revision.

Provide small context packets: original brief, approved contract, current task, permitted paths/actions, relevant interfaces, failure evidence, and budgets. Avoid copying every repo and transcript into every prompt. Record model/provider identity, prompt template version, task output, tool events, usage, and cancellation state. Store operational rationale and decisions; private model reasoning is neither required nor a source of scientific authority.

## 10. Local hardware deployment

The controller and artifact store can live on WSL; the Mac can use a CLI over an authenticated connection or SSH. Do not share a SQLite WAL database over a network filesystem. [S6] Treat the 4090 as one exclusive heavy-job resource initially. CUDA/WSL and container capability must be tested on the installed runtime. [S7]

A GPU slot is a scheduling rule for Kestrel jobs, not guaranteed host-wide VRAM isolation. Unmanaged Windows/WSL processes can still consume GPU memory. Report this limitation and detect external contention where possible. Do not silently change the scientific job shape after OOM.

macOS supports core development and CPU acceptance tests. It must not be reported as satisfying a Linux container-isolation gate without actually testing that deployment. Linux/WSL is the initial unattended execution target; high-assurance mutually untrusted workloads require a stronger separately validated sandbox/VM profile.
