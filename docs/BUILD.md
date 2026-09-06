# Build sequence

## 1. What the human does

Create an empty repository, provide this specification, authorize a bounded development environment, and review the small trust boundary before enabling live unattended work. Do not manage the implementation subtask queue. The coding agent owns decomposition and ordinary execution.

Suggested shell sequence after downloading the kit:

```bash
mkdir -p ~/projects
cd ~/projects
mkdir kestrel
cd kestrel
git init -b main
# Set KIT to the real downloaded ZIP path before running the next line.
python3 -m zipfile -e "$KIT" .
python3 tools/validate_pack.py
git add .
git commit -m "Define Kestrel product and build contract"
git switch -c build/first-pilot
codex
```

Use `claude` instead of `codex` when appropriate. These assume the chosen coding tool is already installed and authenticated. The extraction target must be empty. The current kit contains specifications, not a distributable `kestrel` Python application.

Paste the contents of `prompts/BOOTSTRAP.md`, or ask the coding agent to read that file and execute it. Interactive Goal mode is optional; it is not part of Kestrel's runtime architecture.

## 2. Development operating rules

Keep the release under construction separate from a deployed controller. The builder may edit and test source code; it may not turn that source-edit privilege into authority to deploy, approve live workloads, or modify unrelated projects.

The builder maintains BUILD_STATE.md and an implementation task list. It may refine ordinary tasks without human approval. It must not silently change this product boundary, scientific criteria, or acceptance semantics. Larger scope changes are proposals.

Commits should be coherent and tested. Implement one vertical capability, verify it, and continue. After a session ends, the resumption prompt reconstructs progress from files and actual tests. Do not depend on a provider's transcript as the only project state.

## 3. Agent-executed increments

These are build order, not requests for human approval after every increment.

### M0 — Executable skeleton and external-world fixtures

Build packaging, strict basic schemas, CLI, developer checks, and deterministic mock-agent support. Generate two tiny external repositories in pytest temporary directories outside the source root. One should produce numerical observations; the other should support a qualitatively different verification task, such as checking a proposition/counterexample or a small decision simulation.

Implement a narrow offline demo: register a generated external project, execute a known trusted fixture command through the development driver, ingest an artifact, and render a result. Do not call this adversarial isolation.

The first deliverable is a real vertical slice, not 40 empty interfaces. Preserve the ability to run all core tests without CUDA, Docker, or live model credentials.

### M1 — Research contracts, source identity, artifacts, and provenance

Implement original brief preservation, explicit contract interpretation, external sidecars, source snapshots, candidates, attempts, strict response schemas, artifact hashes, and evidence references. Registering a project must be non-executing. Dirty source handling must be explicit.

Demonstrate two candidates changing the same relative filename without affecting the original external repository or each other. Initially the development driver can exercise known fixture programs, but do not enable arbitrary generated code yet.

Introduce SQLite state with versioned migrations and a single authoritative writer. Use separate configuration/runtime directories; reject runtime roots nested under the framework source root or active source roots when that would violate the boundary.

### M2 — Enforced authority, sandboxing, resources, and cancellation

Implement one real Linux container backend, job capability probes, a deployed-principal model, contract-bound approvals, task/campaign budget reservations, restricted mounts/network, and cancellation of whole job trees. An isolated profile must fail closed when its guarantees are unavailable.

Test actual forbidden reads/writes, evaluator/controller protection, egress denial, limits, and cleanup. Do not give workers the container socket. Do not pretend GPU scheduling is GPU memory partitioning. If storage quotas are advisory rather than hard, expose that fact and reject contracts requiring hard quotas.

Do not modify host services/drivers or install privileged packages without permission. On machines lacking the required isolation runtime, implement and test mock/development behavior and report the deployment gate as blocked.

### M3 — Scientific evaluation and negative-result integrity

Implement evaluator registrations independent of candidates, protected references/targets, numerical recomputation, analysis versioning, exploration/confirmation contract changes, selection history, and structured findings. Add counterexample/known-oracle checks and claim-to-evidence validation.

Demonstrate an honest treatment that performs worse and still completes the campaign. Demonstrate a fabricated metric being rejected. Distinguish execution success, protocol validity, finding, and assurance level.

The first pilot does not need a universal statistics library. Supply a simple explicitly justified analysis for the synthetic fixtures and a protocol interface for future domain methods.

### M4 — Bounded coding-agent integration

Use the same TaskSpec/Attempt boundary for a mock agent and one live provider backend. Implement Codex first if it is available, using verified official SDK or non-interactive interfaces. Add Claude through the same boundary and conformance tests, not a second controller architecture.

Capture structured output, usage, tool events where available, timeouts, cancellation, errors, and provider version. Agents produce proposals and candidate artifacts, not trusted approvals. Prevent project-provided settings/hooks from silently expanding host authority.

Offline tests simulate malformed responses, refusal, tool failure, timeout, provider outage, quota exhaustion, and session interruption. A real provider test requires explicit budget and a credential boundary that does not expose reusable secrets to untrusted commands. Lack of credentials is not a reason to fabricate live test results.

### M5 — Durable campaign loop, evidence invalidation, and recovery

Implement dynamic planning at bounded decision checkpoints: propose next action -> validate -> reserve -> execute -> verify -> record -> decide. A blocked task should not stop independent authorized tasks. Planner calls are themselves tracked, budgeted work.

Add transactional dispatch, stable backend job identity, reconciliation after crashes, stale lease handling, cancellation, budget accounting across retries, and no duplicate scientific replication from cached results. A recording can replay historical agent outputs; a new model call is not deterministic replay.

Add evaluator/data invalidation propagation, backup/restore, retention and safe garbage collection. Do not build a distributed workflow platform. An eventual Temporal backend should preserve the same external task and evidence contracts.

### M6 — Adoption, packaging, evidence export, and independent audit

Test installation from a built wheel in a fresh environment outside the checkout. Implement a CLI-driven external-project onboarding path, sidecar validation, a complete offline demo, inspect/status/cancel commands, and portable evidence bundles. Include platform support and enforcement reports.

Prepare one live synthetic campaign only after explicit authority and security-gate review. Linux isolation, CPU, macOS core, and optional GPU/provider checks must be reported separately. No user project enrollment is part of this build.

The independent audit must inspect actual code and artifacts, not only green test output. Open high-severity issues block the corresponding deployment mode. No automatic publication or push is required.

## 4. Target CLI

These are requested interfaces to implement. They do not exist in this specification kit.

```text
kestrel doctor
kestrel lab init PATH
kestrel project register --manifest PATH
kestrel project validate PROJECT
kestrel campaign propose --project PROJECT --brief PATH
kestrel campaign inspect CAMPAIGN
kestrel campaign approve CAMPAIGN --digest DIGEST
kestrel campaign run CAMPAIGN
kestrel campaign status CAMPAIGN
kestrel campaign cancel CAMPAIGN
kestrel campaign report CAMPAIGN
kestrel evidence export CAMPAIGN --output PATH
kestrel demo --offline
```

Approval requires an authenticated operator authority, not merely possession of the CLI. Register/propose do not grant execution rights. CLI/API/MCP all delegate to the same application policy checks. Do not implement a bypass path for convenience.

A minimal initial UI can be the CLI plus static Markdown/HTML reports. Add a local read-only dashboard only after the control and evidence loop works. Do not store restricted content in a public dashboard or expose an unauthenticated controller port.

## 5. Release gates

- **Offline pilot ready**: all applicable synthetic and core acceptance tests pass; no provider/GPU claim.
- **Isolated local ready**: actual Linux isolation, authority, resources, and recovery checks pass.
- **Live agent ready**: tested provider integration, explicit data policy, credential boundary and budget enforcement.
- **GPU worker ready**: actual device/runtime and scheduling tests pass on the target machine.

These are capabilities, not a single misleading green label. A Mac developer can complete substantial work without satisfying the Linux deployment gate. The report must state that distinction.

## 6. First live campaign

Use a newly generated external synthetic project. Ask the agent to implement two bounded candidates, with at least one expected negative or counterexample outcome. Predeclare the small budget, baseline, metric, selection rule, and evaluation procedure. Review the final evidence packet and replay it through the independent evaluator.

Do not use the first live run to test a novel frontier research idea. First test whether Kestrel conducts a small known investigation honestly.

## 7. What to postpone

A scheduler that maximizes formal value of information, distributed/cloud/HPC execution, a web app, vector memory, arbitrary plugins, S3, graph databases, model routing across dozens of providers, and automatic paper writing all have plausible future value. None belongs before the external-project and evidence-integrity vertical slice.

Promote a dependency only when its capability is needed. Reuse provider loops, container runtimes, Git, schema libraries and artifact formats. Keep the custom code focused on research contracts, policy decisions, provenance, and evaluation lifecycle.
