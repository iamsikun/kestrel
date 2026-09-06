# Product definition

## 1. The problem

Researchers have more hypotheses than time to implement and evaluate them. Coding agents increase implementation capacity but also increase the number of opportunities for hidden confounding, evaluation overfitting, accidental state sharing, incorrect numerical code, unreviewed external actions, and unsupported conclusions.

Kestrel's product is not a chat transcript or a collection of agents. Its product is an inspectable chain from a question to an authorized investigation to evidence to a scoped decision.

## 2. Three separations

### Software from science

A software task may require fixing a known invariant. A scientific task may legitimately end with a negative finding, a counterexample, or insufficient evidence. “Make the new method win” is not a valid research completion condition.

### Proposal from authority

An agent proposes a task graph, candidate code, tests, analyses, and recommendations. Deterministic deployed services check whether those actions are permitted. The proposal-generating agent cannot grant its own permissions or attest to its own success.

### Framework from projects

Kestrel does not own research projects. A project may be Python, R, Julia, C++, a notebook-derived command, a simulation, a formal proof checker, or an existing remote job. Only the declared integration capabilities are available. No adapter implies more scientific assurance than it actually supplies.

## 3. Four kinds of files

1. Framework source: code, schemas, synthetic tests, docs, provider adapters.
2. Private lab definition: project registrations, briefs, policies, evaluator registrations, credentials references. External to source; optionally a separate private Git repo.
3. Research projects: independent repositories or immutable source packages under their owners' control.
4. Runtime state: controller DB, immutable snapshots, job outputs, artifact store, logs. External to both source and original projects; not committed to Git by default.

Example layout, not fixed paths:

```text
~/projects/kestrel/              # framework source only
~/research/project-a/            # independently owned
~/research/project-b/            # independently owned
~/kestrel-lab/                   # private definitions
/var/lib/kestrel/                # service-owned state on a deployed Linux host
```

A developer-mode state directory may be user-owned. That does not provide the same boundary as a separate deployed service identity. The software must say which profile is active.

## 4. Adoption without migration

Support progressive adoption:

- **Import**: record existing runs and artifacts. Mark them externally supplied, not independently executed.
- **Wrap**: describe existing commands in an external sidecar manifest. No changes to the user's repo required.
- **Instrument**: optionally add an adapter to the external project for finer metrics, checkpointing, or evaluation.
- **Extend**: add a domain pack with methodology checks and evaluators. Domain packs remain versioned extensions, not hidden privileged code.

The default enrollment flow reads repository metadata only. It must not run setup.py, install packages, execute hooks, or import the project to discover it. Any active probe is a separately authorized sandbox task.

## 5. Research actions

The high-level planner may propose literature search, assumption clarification, derivation, counterexample search, simulation, implementation, replication, measurement, analysis, or stopping. These are not all implemented as training jobs. External lab actuation, medical interventions, live trading, emailing, publication, and data acquisition with legal or privacy consequences are outside first-pilot autonomous scope.

The next action should be chosen for its expected ability to resolve an important uncertainty relative to its cost—not simply for its predicted chance of a positive metric. Begin with an explainable priority queue and explicit strategic priorities; do not fabricate numerical information-gain estimates where they cannot be estimated credibly.

## 6. Minimal user experience

A user should be able to register a project, describe a question, inspect the proposed interpretation and permission envelope, authorize a campaign, and receive a decision packet. Routine implementation, scheduling, testing, bounded retries, and reporting are autonomous inside that envelope.

A decision packet answers: What was asked? What actually changed? What was controlled? What was tried? Which checks ran? What happened? How uncertain is it? What would falsify it? What remains unverified? What action is worth taking next?

## 7. First-pilot product success

Do not measure success by lines of code, number of agents, number of tests, or quantity of generated reports. Measure setup friction, isolation failures detected, invalid conclusions rejected, orphan and duplicate jobs avoided, evidence completeness, valid negative results retained, cost accounting, and human interruptions per campaign.

A working first pilot must not depend on any existing personal project. It must demonstrate heterogeneity using two generated external programs and permit an ordinary human-written plan in place of an LLM.
