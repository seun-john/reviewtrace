# Architecture

```
            CLI (typer/rich)          MCP server
                   \                     /
                    ▼                   ▼
                 reviewtrace.core  ──────────►  reports/  (terminal, markdown, json, csv,
                    │                            matrix, response letter)
   ┌────────────────┼──────────────────────┐
   ▼                ▼                      ▼
extractors/       diff/                analysis/  ──►  semantic/ (optional)
(docx, ooxml,     (sections,           (classifier, request_spec,
 comments,         paragraphs,          evidence_finder, heuristics,
 report, yaml)     tables, refs)        confidence)
   │                │                      │
   └────────► models/ (pydantic) ◄─────────┘
```

Parsing, comparison, assessment and reporting are separate layers; `core.py` is the only place
that wires them together.

## Data flow of an audit

1. `extractors.docx.load_document` opens the file read-only and builds a `Document`: sections,
   numbered body paragraphs, tables (with captions), references, tracked changes, footnotes and,
   optionally, Word comments with anchors.
2. Issues come from Word comments, a reviewer report, or `issues.yml`
   (`extractors.issues.build_issue` classifies each and decomposes compound requests).
3. `diff.document_diff.diff_documents` matches sections (heading, then content and position),
   aligns paragraphs inside matched sections, matches the leftovers document-wide (→ *moved*), and
   compares tables and reference lists.
4. `analysis.heuristics.assess_issue` locates each issue's target, applies the category rule and
   returns a validated `Finding`.
5. `core.run_audit` collects findings (an exception in one becomes an `ERROR` finding), optionally
   consults a semantic reviewer, and returns an `AuditReport` that records the SHA-256 of each
   input.

## Locators

There are no page numbers: DOCX does not store them and they depend on rendering. Locations are
logical — section (id + heading), paragraph number (1-based count of non-empty body paragraphs),
table index and cell. A `Location` renders as `Section 2.1 Theoretical Framework, paragraphs 7–8`.

## OOXML reading

`extractors/ooxml.py` walks the body once, in document order, producing paragraphs, tables,
comment ranges (`commentRangeStart/End`, `commentReference`) and tracked revisions (`w:ins`,
`w:del`, `w:moveTo`, `w:moveFrom`). The document text is the **final view**: insertions included,
deletions excluded. python-docx opens the package, validates it and resolves style names; it has
no API for the rest.

## Extension points

- New assessment rule: `analysis/heuristics.py` (see CONTRIBUTING).
- Semantic provider: subclass `SemanticReviewer`.
- New report format: add a renderer under `reports/`; build on `reports.matrix.build_matrix`.
- New input format (PDF annotations, Google Docs): produce `ReviewIssue` objects via
  `extractors.issues.build_issue` and a `Document`; the rest of the pipeline is format-agnostic.
