# Security and authority model

## 1. Threat model and limitations

Plan for accidental bugs, hallucinated commands, malicious instructions in repositories/documents, compromised packages, fabricated worker output, adversarial generated programs, and ordinary process failures. Do not assume an adversarial host administrator, kernel exploit, or hardware compromise can be defeated by this application alone.

Local personal-research and mutually untrusted multi-tenant deployments are different assurance targets. The first release is single-operator, Linux/WSL-first. Containers reduce exposure but are not a universal strong isolation boundary. Report the tested enforcement profile and residual risks. [S8-S10]

## 2. Separate principals and deployments

The deployed controller owns policy, approval records, state, resource reservations, evaluator registrations, and evidence publication. Its runtime and credentials are outside worker namespaces. Workers do not mount the controller source tree, DB, private lab configuration, policy files, home directory, SSH agent, host drives, or container daemon socket.

The user approves a contract/permission envelope through a separate authenticated operator path. Agents may submit requests but cannot call the operator approval operation with a worker credential. A policy file writable by a worker is not policy enforcement.

Development agents can edit controller code in this source repo. That edit does not update the running controller. Deployment requires a separately reviewed build artifact and operator action. A controller-upgrade campaign cannot certify or deploy itself.

## 3. Execution profiles

- **development**: trusted synthetic fixtures in local subprocesses. Explicitly no adversarial isolation assurance.
- **isolated-local**: verified Linux container profile with separate workspaces, protected mounts, enforced resources and restricted network. Required before autonomous execution of generated code.
- **hardened**: stronger sandbox or VM plus validated egress and credential brokerage, intended for sensitive/mutually untrusted workloads. Not a first-pilot claim.

The controller must reject a task whose required properties are unavailable. No automatic fallback from an isolated profile to a plain subprocess. Tests must verify actual behavior: forbidden reads fail, writes fail, egress fails, limits stop work, and host secrets are not visible.

## 4. Network and credential separation

Use separate authority for source acquisition, networked reasoning, dependency builds, offline execution, and evaluation. Research data readable by a coding agent can enter its model requests; blocking arbitrary internet access alone does not solve this. Enforce allowed model providers and data classifications before giving a task such access.

Use an existing credential-injecting proxy or short-lived least-privilege mechanism where provider support permits. Do not create a universal API gateway in the first milestone. Root provider credentials must not be in the environment of arbitrary build scripts/tests; official Codex guidance explicitly warns about this. [S1] Claude's deployment guidance explains the external proxy boundary. [S8]

If a backend requires exposing reusable credentials to untrusted code, it cannot meet the stronger live-agent profile. Mark the backend blocked or restrict the demonstration to reviewed development use; do not call the profile secure. Provider authentication and billing mode must be explicit. Do not assume a chat subscription covers unattended API usage.

A networked research reader returns evidence with source provenance. Web pages, READMEs, tool descriptions, and retrieved instructions cannot expand capabilities. Approved provider access is still an egress path for data; sensitive data require explicit provider policy and may be excluded entirely.

## 5. Resources and money

Enforce task and campaign caps, including retries. Reserve worst-case declared request tokens/calls and compute slots before dispatch. Track CPU time or elapsed job budget as defined in policy; distinguish hard limits from advisory estimates. Count completed, failed, cancelled, and abandoned attempts.

A money cap requires a known pricing/billing model and bounded requests. Where exact billing is unavailable, enforce token/call/runtime caps and report dollar estimates as estimates. Cancellation cannot necessarily stop billing for an in-flight provider request; reserve for that possibility.

CPU/RAM/PID limits need runtime enforcement. Docker containers are not resource-limited by default. [S9] Workspace/storage quotas and total host disk monitoring must be verified; a polling watchdog is not a strict filesystem quota. Mark the enforcement quality in the profile and reserve safe headroom. OOM may stop a run; it never authorizes changing scientific parameters.

GPU admission controls Kestrel scheduling. It does not partition all host VRAM or prohibit unmanaged processes. Do not make that stronger claim.

## 6. Evaluator boundary

During prediction the candidate sees permitted inputs but not sealed targets. Terminate or isolate candidate execution before a trusted evaluator receives predictions and target data. Validate artifact shape, IDs, counts, bounds and formats; prohibit arbitrary pickle execution in trusted evaluation. Evaluation code that must execute a submitted model does so in an additional sandbox with carefully minimized access, not directly beside sensitive labels and credentials.

Evaluator and analysis artifacts are pinned independently of candidate code. Changing them requires registration and, where applicable, new approval. Re-evaluation creates new evidence; it does not overwrite the old result.

Keep separate authority for opening final data. A quota does not by itself confer valid statistical inference. Record every access and use the approved analysis plan.

## 7. Provenance and reports

Hash content after ingestion and bind it to a verified attempt. Never trust a worker's self-reported score as certified evidence. Even an intact hash chain cannot establish mathematical correctness or defend against an administrator rewriting all state.

Redact secrets and restricted paths before logs reach external models or reports. Reports may contain only authorized data. Add retention policies, backup/restore tests, safe garbage collection, and deletion tombstones so storage cleanup does not silently make a result unreproducible.

## 8. Security gates before live autonomy

Required: original repo unchanged; cross-worker reads/writes denied; controller policy/state/credentials inaccessible; forbidden network denied; resource limits tested; cancellation confirmed; evaluator tampering rejected; malformed/path-traversing artifacts rejected; approvals bound to exact contract and principal; no secret exposure through provider subprocess inheritance.

High-consequence domains require their own policies and review. Kestrel does not infer legal, ethical, or safety authorization from the fact that a project repository is registered. No live trading, external publication, wet-lab actuation, or clinical action in the first release.
