# ADR-02: Zero-Shot Classification over a Fine-Tuned Model

**Decision:** Use Hugging Face zero-shot classification for filing domain triage instead of a
fine-tuned classifier.

**Rationale:** No labeled training data exists at project start. Zero-shot classification lets
triage ship early without waiting on a labeling effort, and reaches acceptable accuracy on the
four-class domain problem.

**Tradeoff:** Lower accuracy ceiling than a fine-tuned model. Plan to collect human-reviewed
labels via the classification feedback loop and revisit a fine-tuned classifier in V2.
