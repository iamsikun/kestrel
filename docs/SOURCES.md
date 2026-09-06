# Primary sources

Retrieved September 6, 2026. The architecture is a proposed design; these sources support the referenced library capabilities and methodological cautions, not a claim that Kestrel has been implemented or validated. Check current versions again at implementation time.

- **S1 — OpenAI: Codex non-interactive mode.** Programmatic execution, JSON output, output schemas, and credential cautions.
  `https://developers.openai.com/codex/non-interactive-mode`
- **S2 — OpenAI: Codex SDK.** Programmatic agent integrations.
  `https://developers.openai.com/codex/sdk`
- **S3 — Anthropic: Agent SDK overview.** Reusable agent loop and Python/TypeScript integrations.
  `https://code.claude.com/docs/en/agent-sdk/overview`
- **S4 — Temporal: Workflows.** Durable workflow execution and recorded event history.
  `https://docs.temporal.io/workflows`
- **S5 — Temporal: Activities.** External side effects, retries, and idempotency requirements.
  `https://docs.temporal.io/activities`
- **S6 — SQLite: Write-ahead logging.** Same-host requirement and network-filesystem limitations.
  `https://sqlite.org/wal.html`
- **S7 — NVIDIA: CUDA on WSL user guide.** WSL GPU/container support and Windows driver boundary.
  `https://docs.nvidia.com/cuda/wsl-user-guide/index.html`
- **S8 — Anthropic: Securely deploying AI agents.** Threat model, isolation, least privilege, and credential proxies.
  `https://code.claude.com/docs/en/agent-sdk/secure-deployment`
- **S9 — Docker: Resource constraints.** Resource limits are not automatic defaults.
  `https://docs.docker.com/engine/containers/resource_constraints/`
- **S10 — Docker: Engine security.** Daemon privilege and attack-surface considerations.
  `https://docs.docker.com/engine/security/`
- **S11 — Cawley and Talbot, JMLR (2010): On Over-fitting in Model Selection and Subsequent Selection Bias in Performance Evaluation.** Selection bias in evaluating searched methods.
  `https://jmlr.org/papers/v11/cawley10a.html`
- **S12 — Hypothesis: Stateful tests.** Model-based testing of action sequences.
  `https://hypothesis.readthedocs.io/en/latest/stateful.html`
- **S13 — Astral uv: Structure and files.** Project and lockfile structure.
  `https://docs.astral.sh/uv/concepts/projects/layout/`
- **S14 — LangGraph: Persistence.** Agent state checkpointers and stores; useful optional reasoning infrastructure, not scientific authority.
  `https://docs.langchain.com/oss/python/langgraph/persistence`
- **S15 — Model Context Protocol: Architecture overview.** Optional interoperability at the tool interface.
  `https://modelcontextprotocol.io/docs/learn/architecture`
- **S16 — OpenTelemetry: What is OpenTelemetry?** Optional telemetry interoperability.
  `https://opentelemetry.io/docs/what-is-opentelemetry/`
- **S17 — Sakana AI: The AI Scientist.** Authors describe incorrect implementations, unfair comparisons and reporting errors as limitations.
  `https://sakana.ai/ai-scientist/`

No current prices, guaranteed GPU performance, package-name availability, commercial license suitability, or universal security guarantees are asserted by this kit.
