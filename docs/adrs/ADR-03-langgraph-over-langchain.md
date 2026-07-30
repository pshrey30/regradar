# ADR-03: LangGraph over Vanilla LangChain

**Decision:** Use LangGraph for multi-agent orchestration instead of a linear LangChain chain.

**Rationale:** The pipeline needs conditional routing (e.g., skipping RAG retrieval for
low-priority filings), per-node retry, and human-in-the-loop interruption for Critical filings.
LangGraph's typed state graph supports all three without custom state-management code.

**Tradeoff:** Steeper learning curve and more boilerplate than a simple chain. Justified because
agent orchestration is the core value proposition of the product, not an incidental detail.
