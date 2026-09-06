# Implementation decisions

## 2026-09-06 — first pilot

- Preserve the supplied specification files and their integrity manifest unchanged.
  Implementation instructions and results live in new documents.
- Use Python >=3.12 with strict Pydantic v2 contracts, SQLite, argparse, and immutable
  JSON/file artifacts. CLI needs no additional framework. Pin resolved dependencies
  after smoke tests; provider libraries remain absent until a working authorized test.
- Development execution accepts only exact framework-generated fixture sources. It
  provides no adversarial isolation. Arbitrary project or agent-produced code requires
  an actually measured isolated backend; an unavailable profile fails closed.
- Fixtures are deterministic numerical prediction and integer counterexample search.
  Comparison is exhaustive over a finite synthetic domain; no population statistical
  inference or general scientific validity is claimed. A deliberately inferior
  treatment must complete with `not_supported`.
- Operator tokens and databases in developer labs demonstrate controller policy logic,
  not a deployed service identity boundary. No deployment endpoint or self-upgrade
  operation is exposed. Source editing does not update a deployed controller.
- Check official Pydantic strict-mode, Hypothesis stateful testing and uv layout docs
  before use. References: https://docs.pydantic.dev/latest/concepts/strict_mode/,
  https://hypothesis.readthedocs.io/en/latest/stateful.html,
  https://docs.astral.sh/uv/concepts/projects/layout/ (checked 2026-09-06).
