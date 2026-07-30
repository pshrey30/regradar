# ADR-04: Tiered Model Routing

**Decision:** Route Critical/High risk filings to GPT-4o and Low/Medium risk filings to a
cheaper Hugging Face-hosted model (Granite-13B).

**Rationale:** GPT-4o costs roughly 10x more per call than the cheaper tier, and the majority of
filings are expected to be Low/Medium risk. Routing by risk level keeps spend proportional to
how much a filing actually matters.

**Tradeoff:** Some quality degradation on low-priority filings versus using GPT-4o everywhere.
Measured and accepted as a worthwhile cost/quality tradeoff once real eval numbers are available
(see `eval_runs` and the Langfuse cost dashboard).
