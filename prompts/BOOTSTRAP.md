# Implement Kestrel's first pilot

You are the lead implementation agent for a new standalone research framework called Kestrel.

Read AGENTS.md, README.md, docs/PRODUCT.md, docs/ARCHITECTURE.md, docs/PROTOCOL.md, docs/SECURITY.md, docs/SCIENCE.md, docs/BUILD.md, specs/milestones.json, and specs/acceptance.json.

Build this from an empty framework repository. Do not inspect or build on llm-lab or any ongoing user research project. No research project belongs inside this source repository. Generate temporary external projects for tests.

## Your autonomy

Own ordinary implementation decisions and task decomposition. Inspect the actual environment, choose the smallest design satisfying the requirements, create a dependency-aware implementation task list, and implement it. Do not return only a plan. Do not ask me to approve each subtask or document.

Maintain BUILD_STATE.md with what exists, what has actually been verified, commands/results, blockers, and the next unblocked action. Record consequential design choices in docs/DECISIONS.md. Make coherent tested commits on the current build branch. Do not push, publish, deploy, or change host services without permission.

Continue to the next unblocked task after completing one. If an external permission/runtime/credential is unavailable, record that gate accurately and continue useful offline work. Do not downgrade security, fabricate live results, or treat a skipped required gate as passed.

## Build strategy

Implement M0-M6 in dependency order, as vertical slices. Start with a mock agent and two generated external project fixtures. Use actual commands and artifacts, not a fake all-success result. Introduce abstractions only when a working slice uses them.

Use strict typed contracts, a small Python single-host controller, transactional SQLite state, immutable artifacts, external sidecar integration, one tested container backend, and bounded coding-agent adapters. Verify current official provider APIs before using them. Core installation must not require a model account, CUDA, PyTorch, Docker, or an ML framework.

Implement the research semantics ourselves; reuse mature libraries for provider agent loops, serialization, testing, containers, and source control. Do not write a new distributed orchestration system or a universal training framework.

## First-pilot acceptance

The system must register and wrap external projects without modifying them; isolate concurrent candidates; preserve the original brief and exact contract; enforce task/campaign authority and budgets; recover from interruption without blindly duplicating jobs; independently evaluate outputs; reject tampering and fabricated metrics; account for all attempts; retain a negative finding as a valid result; and produce an exportable evidence packet.

The machine-readable acceptance file defines required scenarios. Write adversarial tests that would fail against plausible broken implementations. No worker-controlled evaluator, approval file, metric, or test override can be the sole basis for certification.

Separate developer-mode, isolated-local, live-agent, and GPU assurance. Run only authorized resource/network operations. Normal development dependency setup can use approved registries; offline tests and fixture campaigns must not access the network after setup. Use synthetic public data only.

## Human escalation

Escalate only when resolving a consequential scientific ambiguity, weakening/changing an invariant, changing the product boundary, expanding data access, adding expensive external services, installing privileged host components, or deploying the trusted controller. Routine naming, module boundaries, test additions, bounded local debugging, and refactoring are yours to decide.

## Completion report

Provide the exact runnable offline demo and setup commands, acceptance evidence, tested platforms/provider versions, security properties actually enforced, skipped/blocked checks, unresolved risks, and the smallest operator action needed for the next deployment gate. Do not claim the framework is scientifically correct in general or universally secure.

Begin now by checking the environment and implementing the first vertical slice.
