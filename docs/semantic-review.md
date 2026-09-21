# Semantic review (optional, disabled by default)

Version 0.1.0 contains the interface and the policy for semantic review. It contains **no
provider adapters and no code that performs network I/O**, and the CLI has no switch that enables
one. A caller who wants semantic review writes (or installs) an adapter, constructs it, and
passes it to `run_audit(..., semantic=adapter)` or one of the `audit_*` functions in
`reviewtrace.core`.

## Interface

```python
from reviewtrace.semantic import SemanticReviewer, build_payload, parse_assessment
from reviewtrace.models import SemanticAssessment


class MyReviewer(SemanticReviewer):
    name = "my-model"

    def assess(self, issue, original_context, revised_context, evidence) -> SemanticAssessment:
        payload = build_payload(issue, original_context, revised_context, evidence)
        raw = call_my_model(payload)  # your transport
        return parse_assessment(self.name, raw)  # validates the structured output
```

`build_payload` is provider-independent: the reviewer request, anchor, original and revised
context, the detected changes (indexed) and a JSON schema for the answer. The model is never
asked for a bare yes/no.

The semantic reviewer is consulted only for findings that are `NEEDS_REVIEW`, or
`NOT_ASSESSABLE` with evidence. It is never consulted for `RESOLVED`, `UNRESOLVED` or `ERROR`.

## Policy (`merge_assessment`)

- A HIGH-confidence deterministic `RESOLVED`/`UNRESOLVED` is not overridden.
- A semantic `RESOLVED`, `PARTIALLY_RESOLVED` or `UNRESOLVED` must cite `evidence_refs` that
  exist; otherwise it becomes `NEEDS_REVIEW`.
- A semantic `RESOLVED` gets the cited evidence attached as supporting evidence, so the
  "every RESOLVED has evidence" invariant still holds.
- Confidence is capped at `MEDIUM`; a LOW-confidence `RESOLVED` becomes `NEEDS_REVIEW`.
- The finding is marked `assessment_source: semantic`, keeps `deterministic_status`, and always
  `requires_human_review`.
- If the reviewer raises, the deterministic finding is kept and a note is added to the report.

## Privacy

Documents must not leave the machine silently. An adapter is a deliberate, explicit choice by
whoever writes the calling code; nothing in ReviewTrace instantiates one.
