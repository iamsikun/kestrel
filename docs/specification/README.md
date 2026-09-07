# Kestrel
## An evidence-first research runtime

**Status: build specification, not an implemented framework.** This kit is an original proposal for a new, standalone project. No existing research repository is a dependency or template. The working name is not a claim that package names or trademarks are available.

Kestrel should turn a scientific brief into an authorized research campaign, isolate its candidate implementations, execute and evaluate work, and produce evidence-backed decisions. It should work with independent repositories and multiple coding-agent providers. It should not become a universal trainer, an unbounded autonomous shell, or a system that equates positive findings with success.

## Start here

1. Extract this kit into an empty Git repository.
2. Run `python3 tools/validate_pack.py` to verify this specification kit.
3. Read `docs/BUILD.md`, especially the first-pilot boundary.
4. Give the coding agent `prompts/BOOTSTRAP.md`.
5. Let it decompose and implement unblocked work. Human review is for authority changes, consequential scientific choices, and deployment—not every internal task.
6. Before live unattended use, perform the independent audit in `prompts/AUDIT.md`.

The `kestrel ...` commands in the documents are **target interfaces to implement**, not commands provided by this kit. The validator checks the kit's integrity and internal references; it does not establish that the framework exists or works.

## Non-negotiable boundaries

- The framework source repository contains framework code, protocols, tests, synthetic fixture generators, and documentation only.
- User research repositories remain external. No vendoring, submodules, symlinks, or editable mounts of active user projects inside the framework source tree.
- Lab configuration, private briefs, registry entries, runtime snapshots, results, credentials, and controller databases live outside the framework source tree.
- Agents propose actions; a deployed controller authorizes and records actions.
- Workers do not receive controller database access, policy-edit authority, evaluator-authoring privileges, host credentials, or final labels.
- A valid negative or inconclusive result is a successful research outcome.
- No method or test suite makes arbitrary scientific claims automatically true. Assurance must state exactly which checks ran and what remains unverified.

## Document map

| File | Purpose |
|---|---|
| `AGENTS.md` | Rules for agents building Kestrel |
| `docs/PRODUCT.md` | Product boundary and adoption model |
| `docs/ARCHITECTURE.md` | Components, identities, durable execution, evidence |
| `docs/PROTOCOL.md` | External project integration contract |
| `docs/SECURITY.md` | Threat model and actual enforcement requirements |
| `docs/SCIENCE.md` | Scientific validity and interpretation rules |
| `docs/BUILD.md` | Agent-executed implementation sequence |
| `specs/milestones.json` | Dependency-ordered delivery increments |
| `specs/acceptance.json` | Adversarial first-pilot acceptance conditions |
| `examples/` | Illustrative manifests and policies, not registrations |
| `prompts/` | Implementation, resumption, and independent review prompts |
| `docs/SOURCES.md` | Primary documentation supporting technical choices |

## First-pilot definition

A fresh install should complete an offline campaign against two generated external projects, isolate conflicting candidate changes, recover from an interrupted job, reject a tampered evaluator and fabricated metric, account for every attempt, and report a negative finding honestly. A live coding agent is an optional subsequent gate; lack of credentials must not block completion of the offline pilot.

## Deliberately not in the first pilot

Multi-tenant SaaS, Kubernetes, a new general-purpose workflow engine, a vector database, a GPU training stack, automatic trading, external publication, physical-laboratory actuation, a custom model API gateway, universal statistical inference, or self-deployment of modified controller policy.
