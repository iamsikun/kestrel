# Independently audit Kestrel

Read the original product, architecture, protocol, security, science and build documents, and specs/acceptance.json. Inspect the actual implementation and generated artifacts. Do not trust a completion claim, a test count, or a builder-authored summary.

Work in a separate review workspace. Review the exact candidate revision. Initially do not edit it; produce reproducible findings and adversarial tests. If you later patch the implementation, mark yourself as an author of that revision and require another independent review for any trust-boundary changes.

Focus on:

1. User projects truly remain external; registration does not execute project code.
2. Sidecar or repo content cannot obtain controller privileges or run on the host.
3. Candidate workspaces, caches, environments and outputs are isolated.
4. Frozen identity binds actual source, environment, data, contract and evaluator.
5. The controller is the sole authority for state, approvals, leases and budget accounting.
6. Workers cannot read final labels, edit evaluators, rewrite policy or access provider credentials.
7. Container/process settings enforce the claimed restrictions in practice.
8. Crash windows, expired leases, cancellation and duplicated provider responses cannot silently duplicate or falsely certify jobs.
9. Claimed metrics are independently computed from validated artifacts.
10. Negative results and failed trials cannot be selectively erased or recategorized.
11. Adaptive search history, replication units and selection rules are preserved.
12. Cache hits are not independent replications.
13. Invalidation propagates to dependent claims and reports.
14. Evidence imports/exports preserve trust labels, permissions and reproduction limitations.
15. Live model claims are backed by actual authorized tests and current pinned APIs.
16. The source build agent cannot deploy or approve its own weakened controller.

Use intentionally wrong programs, forged outputs, resource violations, policy edits, interrupted launches, and known counterexamples. Avoid real destructive actions or exfiltration; use local sentinels and synthetic data.

Return a review packet containing tested revision, issues with severity, reproduction commands, new tests, exact deployment gates that pass/fail, and residual risks. Do not change acceptance requirements to make a release pass. A second LLM review is not a mathematical or security proof; state its limits.
