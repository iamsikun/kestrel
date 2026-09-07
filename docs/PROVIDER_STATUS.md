# Provider integration status

The mock and replay adapters use one typed `AgentTask` / `AgentResult` boundary.
Raw recorded Codex JSONL and serialized Claude result readers are implemented;
tests use explicitly labeled synthetic wire fixtures. Their offline execution uses
the same approved TaskSpec/Attempt ledger as the mock, with reserved budgets,
durable outputs or failure diagnostics, and conservative interruption recovery.
They do not invoke a model, execute suggested tools, run project hooks, or create
permissions. Historical usage in replay events is distinct from new calls and
tokens, which are zero. The finite trusted parser measures elapsed time after
return; it is not a hard timer for an arbitrary provider callback.

Checked official sources on 2026-09-06:

- [Codex non-interactive mode](https://developers.openai.com/codex/non-interactive-mode)
  documents JSONL events and structured final output.
- [Claude Agent SDK Python reference](https://code.claude.com/docs/en/agent-sdk/python)
  documents serialized result fields and usage.
- [Claude structured outputs](https://code.claude.com/docs/en/agent-sdk/structured-outputs)
  requires explicit structured output validation, including when a result reports success.

No provider SDK is installed or pinned as working, no model identifier is selected,
and no live test has run. A28 remains incomplete for actual captured provider
conformance; documentation-based synthetic events are not mislabeled as live captures.
A30/A31 require a separately authorized budget, provider/data policy, independently
reviewed credential boundary, actual sanitized captures, and a working pinned
provider version. The live entry point fails closed. There is no automatic
credential discovery or fallback to ambient provider authentication.
