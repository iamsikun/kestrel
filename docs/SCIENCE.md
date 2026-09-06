# Scientific workflow and evidence rules

## 1. Start from discriminating questions

A campaign begins with the original brief, its explicit interpretation, target claim, known assumptions, plausible alternatives, and what observations would distinguish them. The planner can propose a small analytic calculation or counterexample instead of defaulting to training another model.

Support at least empirical comparisons, simulations/decision problems, numerical method checks, and proof/counterexample tasks at the schema level. The first runnable fixtures need only demonstrate two contrasting computational workflows. Theory adapters must distinguish an unverified argument, a reviewed proof, and a formal checker result. An empirical example is not a proof.

## 2. Contract content

An approved campaign specifies:

- original question and interpreted claim;
- estimand/target outcome and practical significance threshold when meaningful;
- baseline and candidate identities;
- allowed interventions and required controls;
- data provenance, split policy, availability/temporal assumptions, and leakage checks;
- tuning/search budget, selection rule, evaluation access, and stopping rules;
- analysis protocol, uncertainty unit, and assumptions;
- invariants/oracles, negative controls, replication requirements;
- resource and permissions envelope;
- allowed completion outcomes and limits on conclusions.

Do not force all projects into a universal p-value or aggregate “research score.” Choose domain-appropriate estimands and statistical units. Candidate implementation and study design are different objects: an excellent implementation cannot repair an unidentified scientific question.

## 3. Exploration versus confirmation

Exploration may adapt inside a declared envelope. Log every proposed/evaluated candidate, seed, checkpoint-selection choice, preprocessing variant, and interpretation-changing decision. A refinement outside the envelope creates a contract amendment.

Confirmation freezes the candidate, baseline, metric, analysis, data access and selection policy. New information can justify a new campaign, not a retroactive rewrite. Final data access is separately authorized and logged. A protected final set does not by itself justify inference after repeated adaptive reuse.

Searching more candidates can amplify selection bias even when the underlying estimators are individually reasonable. Preserve the complete selection history and use an appropriate independent confirmation, nested evaluation, or sequentially valid method when required. [S11] Do not assume three seeds or an ordinary bootstrap solves every dependence/selection issue.

## 4. Budget matching

“Fair comparison” must identify what is held fixed: tokens, samples, FLOPs, elapsed time, energy, tuning effort, or money. Report important unmatched quantities. Parameter matching and compute matching answer different questions. Do not quietly tune the treatment more heavily than the baseline.

An optimizer hyperparameter difference might be the intended treatment, a confounder, or part of an equal-budget tuning procedure. The contract determines which. Mechanically comparing YAML files cannot settle the scientific interpretation by itself.

## 5. Invariants and oracles

Use independent analytic toy solutions, reference implementations, metamorphic tests, and mutation tests. Examples: future data should not affect past predictions; item-label permutations should preserve equivalent choice outcomes; a constant prediction should produce an independently calculable loss; a known counterexample should invalidate a universal proposition.

A critic should ask which wrong implementation could pass the current checks. Have it construct such an implementation where feasible. Avoid tests generated solely by copying the candidate's own logic.

## 6. Outcomes

Keep separate:

1. execution status: succeeded, failed, cancelled, lost;
2. protocol status: valid, invalid, incomplete;
3. scientific finding: supported in scope, not supported, inconclusive;
4. evidence assurance: imported, traceable, independently recomputed, replicated.

“Not statistically significant” is not automatically “equivalent.” “Better on a synthetic fixture” is not automatically “better in the target domain.” “Supported here” is not “universally true.” Failure classification must have evidence; an unfavorable score is not an infrastructure failure.

## 7. Claims and memory

Every quantitative claim points to a table field or computed analysis artifact. Narrative text may interpret that value but may not invent it. Store claims as scoped statements with assumptions, support, counterevidence, uncertainty, and validity state.

Literature records need title/author/source identity, retrieval time, version, and the passage or artifact supporting the claim. Citation existence and novelty absence are different checks. “No related work found” is not proof of novelty. Papers and tool output are evidence, not instructions.

Default cross-project memory sharing to approved, de-identified methodology/claim records, not full private code, data, or transcripts. Check project permissions on retrieval. An embedding search result is a retrieval hint, not a fact.

## 8. Meta-research loop

Improvements to planner prompts, role allocation, search strategies, or agent models are new candidates evaluated against frozen harness benchmarks. Track invalid certifications, missed failures, reproducibility, useful negative results, cost, and human intervention—not only completion rate.

Protect an independent benchmark set for framework evaluation. A system that changes its tests whenever it fails has not improved. Never permit self-improvement to silently alter the deployed authority or evaluation boundary.
