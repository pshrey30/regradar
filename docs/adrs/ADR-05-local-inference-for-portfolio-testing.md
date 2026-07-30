# ADR-05: Local Model Substitution for Portfolio-Scale Testing

**Decision:** Support substituting locally-hosted models in place of paid APIs for local
development and demo runs — an OpenAI-compatible local endpoint (e.g., Ollama) standing in for
GPT-4o in the Analysis Agent, and local Hugging Face `transformers` pipelines standing in for
the hosted Inference API in the Triage and Summarization Agents. Toggled via config
(`USE_LOCAL_LLM`, `USE_LOCAL_HF_INFERENCE`), not hardcoded, and off by default.

**Rationale:** RegRadar is built as a portfolio/resume project, not a live production
deployment. OpenAI and the hosted Hugging Face Inference API are the only pieces of the stack
without a durable free tier. Keeping the production-grade design fully intact in the codebase
(GPT-4o for structured extraction per ADR-04, hosted HF models for classification/summarization)
while allowing a config-only swap to local inference lets the project demonstrate the real
architecture without requiring ongoing API spend to run or re-run it. Because
`OPENAI_API_KEY`/`HUGGINGFACE_API_TOKEN` are already required-but-unvalidated string fields
(Settings only checks they're present, not that they're real), a placeholder value is sufficient
to satisfy config validation when local mode is enabled — no real account is needed at all for
that path.

**Tradeoff:** Local open models are lower quality than GPT-4o and the hosted HF models, so eval
results (Ragas faithfulness, ROUGE-L, etc.) generated with local substitution enabled should be
clearly labeled as such wherever they're reported — in the README and `docs/known-limitations.md`
— rather than presented as GPT-4o-equivalent numbers. Running local inference also requires
Ollama (and sufficient local compute) on the development machine, a dependency the production
deployment path doesn't need.

**Implementation:** The actual routing logic that reads these config flags is built as part of
AGENT-02 (Triage), AGENT-07 (Analysis), AGENT-08 (Summarization), and AGENT-09 (tiered routing) —
this ADR records the decision and adds the config surface (FOUND-05) ahead of that, matching how
`TIER_HIGH_MODEL`/`TIER_LOW_MODEL` already existed in Settings before AGENT-09 built the router
that consumes them.
