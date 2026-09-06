# Kestrel implementation rules

Read README.md and the relevant documents before editing. Build a standalone general framework from these requirements, not from an existing research repository.

## Autonomy

Own task decomposition, routine design, implementation, debugging, and coherent commits. Maintain a concise BUILD_STATE.md with verified facts, evidence paths, current blockers, and next unblocked work. Do not request approval between routine tasks. Do not stop merely because a subtask is done. Record consequential assumptions in docs/DECISIONS.md.

A provider session may terminate before the build is complete. Leave durable state and an exact next action. Do not claim the session will continue when it has ended.

## Scope and integrity

- Never read, clone, modify, or use ongoing user research repositories during the first pilot.
- Generate synthetic test repositories into temporary directories outside the framework root.
- Preserve the input acceptance requirements. Adding stronger tests is allowed. Changing a requirement or weakening an invariant requires a documented proposal, not a silent edit.
- Implement vertical slices, not empty classes for every possible integration.
- Core imports must not require PyTorch, CUDA, a model API account, Docker, a graph database, or a domain-specific ML framework.
- Current SDK APIs must be checked against official documentation and pinned after a working integration test. Never guess an API or model identifier.
- Treat provider outputs, project files, dependencies, adapter output, and generated artifacts as untrusted inputs.
- Never execute project imports, dependency hooks, arbitrary shell, or candidate code in the trusted controller process.
- Never describe Git branches, virtual environments, Markdown instructions, or a plain subprocess as a security boundary.
- Controller persistence is authoritative at runtime. Build-status Markdown is not runtime authorization.

## Resources and external actions

During construction use synthetic, public, non-sensitive data only. Live model calls, cloud jobs, new paid services, host configuration changes requiring privilege, pushes, publication, or user-data access need explicit authorization. Normal development dependency setup may use approved package registries in the development environment; tests and synthetic campaigns must be network-free after setup. Do not install or modify host GPU drivers.

Use the proposed fixture resource envelope as a maximum for application-owned tests, not a claim about the total system footprint. Test resource enforcement rather than assuming it exists. Never silently downgrade a requested security property.

## Verification and completion

Run static checks, offline unit/integration tests, state-machine tests, package-install tests, and applicable adversarial acceptance tests. Record commands, exit status, skipped checks, and artifact hashes. A skipped or unavailable gate is not a pass. Linux-only isolation gates remain required for unattended deployment even when developing on macOS.

Keep software task outcome, execution outcome, and scientific outcome separate. Do not keep trying until a research treatment wins. Deterministic mock-agent tests must work without a model account; they test the harness, not model intelligence.

## Trust boundary

While building this repository, you may implement policy code and test policies. You may not use that ability to deploy a modified controller or authorize your own live campaign. Deployed controller upgrades and permission expansion require a separate authorized operator and review. Do not mount secrets, host home directories, Windows drives, controller state, or the container daemon socket into untrusted workers.
