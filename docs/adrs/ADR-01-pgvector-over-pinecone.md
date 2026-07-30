# ADR-01: pgvector over Pinecone

**Decision:** Use pgvector (a Postgres extension) instead of a managed vector database for
storing and querying filing chunk embeddings.

**Rationale:** Filing metadata, extracted obligations, and chunk vectors are all relationally
tied to the same `filings` row. Keeping vectors in the same database as the relational data
avoids operating, backing up, and keeping a second system in sync.

**Tradeoff:** pgvector is roughly 2x slower than a dedicated vector database once a corpus
exceeds ~10M vectors. Acceptable at RegRadar's V1 corpus size; revisit if the corpus grows past
that threshold.
